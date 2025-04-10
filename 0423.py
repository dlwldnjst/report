import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import requests
import re
from urllib.parse import unquote
from html import escape
from collections import Counter

@st.cache_data(show_spinner=False)
def fetch_all_thumbnails(df_merged, headers, api_url):
    df = df_merged.copy()
    thumbnail_cache = {}
    for idx, row in df.iterrows():
        isbn_val = row.get('ISBN')
        if isbn_val in thumbnail_cache:
            thumbnail_url = thumbnail_cache[isbn_val]
        else:
            book_info, error = fetch_book_thumbnail(isbn_val, headers, api_url)
            thumbnail_url = book_info.get('thumbnail') if book_info else None
            thumbnail_cache[isbn_val] = thumbnail_url
        if thumbnail_url:
            df.at[idx, 'thumbnail'] = thumbnail_url
    return df

# KDC 대분류 매핑 사전 추가
KDC_CATEGORIES = {
    '0': '총류',
    '1': '철학',
    '2': '종교',
    '3': '사회과학',
    '4': '자연과학',
    '5': '기술과학',
    '6': '예술',
    '7': '언어',
    '8': '문학',
    '9': '역사'
}

def calculate_percentile_by_grade(loan_count, grade):
    # 각 학년별 분포 데이터 (전교생 300명 기준으로 정규화)
    loan_distributions = {
        1: {5: 1, 3: 1, 2: 4, 1: 10, 0: 284},
        2: {
            104: 1, 43: 1, 40: 1, 20: 1, 18: 1, 16: 2, 15: 1, 12: 3, 11: 3,
            10: 6, 9: 8, 8: 6, 7: 7, 6: 15, 5: 18, 4: 20, 3: 32, 2: 47, 1: 66, 0: 61
        },
        3: {
            63: 1, 42: 1, 22: 2, 18: 1, 16: 1, 15: 1, 13: 3, 12: 1, 11: 2,
            10: 2, 9: 5, 8: 4, 7: 5, 6: 5, 5: 6, 4: 12, 3: 21, 2: 40, 1: 80, 0: 107
        }
    }

    if grade not in loan_distributions:
        return f"{grade}학년 데이터가 없습니다."

    dist = loan_distributions[grade]
    total_students = 300
    
    # loan_count보다 더 많이 읽은 학생 수
    students_above = sum(count for loan, count in dist.items() if int(loan) > int(loan_count))
    
    # loan_count와 동일하게 읽은 학생 수
    students_equal = sum(count for loan, count in dist.items() if int(loan) == int(loan_count))
    
    # 중간 순위 계산 (동점자는 중간 순위 사용)
    percentile = 100 * (1 - (students_above + students_equal/2) / total_students)
    
    return f"{loan_count}권은 {grade}학년 전체에서 상위 {percentile:.1f}% 입니다."

# 청구기호에서 KDC 대분류 추출 함수
def extract_kdc_category(call_number):
    if pd.isna(call_number) or not call_number:
        return None
    
    # 첫 번째 숫자가 KDC 대분류에 해당
    match = re.search(r'^\s*(\d)', str(call_number))
    if match:
        first_digit = match.group(1)
        return first_digit
    return None

# 가장 많이 읽은 분야 찾기 함수
def find_most_read_category(df):
    if 'call_number' not in df.columns or df['call_number'].isna().all():
        return "분류 정보 없음", {}
    
    # 각 행에서 KDC 대분류 추출
    df['kdc_category'] = df['call_number'].apply(extract_kdc_category)
    
    # 추출된 분류만 선택 (None 제외)
    valid_categories = df['kdc_category'].dropna().tolist()
    
    if not valid_categories:
        return "분류 정보 없음", {}
    
    # 분류별 카운트
    category_counts = Counter(valid_categories)
    
    # 분류명으로 변환한 카운트
    named_counts = {KDC_CATEGORIES.get(cat, f'기타({cat})'): count 
                   for cat, count in category_counts.items()}
    
    # 가장 많이 읽은 분류 찾기
    most_common = category_counts.most_common(1)
    if most_common:
        most_cat, _ = most_common[0]
        return KDC_CATEGORIES.get(most_cat, f'기타({most_cat})'), named_counts
    
    return "분류 정보 없음", {}

# Kakao API로 도서 정보를 가져오는 함수 (썸네일 위주)
def fetch_book_thumbnail(isbn_val, headers, api_url):
    if pd.isna(isbn_val):
        return None, "ISBN 정보 없음"
    isbn = str(isbn_val).replace(',', '').replace(' ', '').lower()
    if not re.match(r'^\d{10}(\d{3})?$', isbn):
        return None, f"유효하지 않은 ISBN 형식: {isbn_val}"

    params = {"target": "isbn", "query": isbn, "size": 1}
    try:
        response = requests.get(api_url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()
        if data.get("documents"):
            api_book_info = data["documents"][0]
            thumbnail_url = unquote(api_book_info.get('thumbnail', ''))
            if not thumbnail_url or not thumbnail_url.startswith('http'):
                thumbnail_url = 'https://via.placeholder.com/100x150.png?text=No+Image'
            return {"thumbnail": thumbnail_url}, None
        else:
            return None, f"ISBN '{isbn}' 검색 결과 없음"
    except requests.exceptions.RequestException as e:
        status_code = response.status_code if 'response' in locals() else 'N/A'
        return None, f"API 요청 실패 (상태 코드: {status_code}): {e}"
    except Exception as e:
        return None, f"ISBN({isbn}) 처리 중 예외 발생: {e}"

def generate_print_view(df_merged, student_name, total_books, grade=None, most_read_category="정보 없음", category_stats=None):
    # 퍼센타일 계산 - 미리 계산하여 HTML 문자열에 직접 삽입
    percentile_text = "?"
    if grade and isinstance(total_books, int):
        try:
            text = calculate_percentile_by_grade(total_books, grade)
            percentile_text = text.split("상위 ")[-1].split(" 입니다.")[0]
        except Exception as e:
            st.error(f"퍼센타일 계산 중 오류: {e}")
            percentile_text = "계산 불가"
    
    # HTML 템플릿 생성 - A4 페이지 레이아웃 및 테두리 개선
    print_html = f"""
    <!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>독서 기록 리스트</title>
        <!-- 폰트 사전 로딩 추가 -->
        <link rel="preload" href="https://fonts.googleapis.com/css2?family=Gowun+Dodum&display=swap" as="style">
        <link href="https://fonts.googleapis.com/css2?family=Gowun+Dodum&display=swap" rel="stylesheet">
        <style>
            /* 전체 문서에 폰트 적용 */
            body, h1, p, div {{
                font-family: 'Gowun Dodum', sans-serif;
                margin: 0;
                padding: 0;
            }}
            
            @media print {{
                body {{
                    background-color: white;
                }}
                
                .book-item {{
                    page-break-inside: avoid;
                }}
                
                @page {{
                    size: A4;
                    margin: 0;
                }}
                
                .print-button {{
                    display: none;
                }}
            }}
            
            .book-item {{
                text-align: center;
                border-radius: 8px;
                padding: 6px;
            }}
            
            .book-item img {{
                width: 70px;
                height: auto;
                max-height: 100px;
                object-fit: contain;
            }}
            
            .book-item p {{
                overflow: hidden;
                display: -webkit-box;
                -webkit-line-clamp: 4;
                -webkit-box-orient: vertical;
                text-overflow: ellipsis;
                line-height: 1.4em;
                max-height: 6em;
                font-size: 0.8em;
                margin-top: 4px;
            }}
            
            .print-button {{
                background-color: #4CAF50;
                color: white;
                padding: 10px 15px;
                border: none;
                border-radius: 4px;
                cursor: pointer;
                font-size: 16px;
                margin: 20px 0;
                display: block;
            }}
            
            .book-grid {{
                display: grid;
                grid-template-columns: repeat(5, 1fr);
                gap: 10px;
                margin: 0 auto;
                max-width: 100%;
            }}

            .container {{
                max-width: 21cm;
                margin: 0 auto;
                padding: 20px;
                box-sizing: border-box;
                position: relative;
            }}

            .page {{
                background-color: white;
                width: 21cm;
                min-height: 29.7cm;
                margin: 10px auto;
                padding: 2cm;
                position: relative;
                box-sizing: border-box;
                box-shadow: 0 0 10px rgba(0,0,0,0.1);
            }}
            
            .page-border {{
                position: absolute;
                top: 1cm;
                left: 1cm;
                right: 1cm;
                bottom: 1cm;
                border: 2px solid black;
                pointer-events: none;
                z-index: 1;
            }}
            
            .page-content {{
                position: relative;
                z-index: 2;
                padding: 1cm;
            }}
            
            .student-info {{
                text-align: center;
                font-size: 18px;
                margin-bottom: 20px;
            }}
            
            .header {{
                text-align: center;
                font-size: 30px;
                margin-bottom: 30px;
            }}
            
            .header h1 {{
                margin-top: 0;
                font-size: 28px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <button class="print-button" onclick="printPage()">인쇄하기</button>
            
            <div class="page">
                <div class="page-border"></div>
                <div class="page-content">
                    <div class="header">
                        <h1>{student_name}의 독서 기록</h1>
                    </div>
                    
                    <div class="student-info">
                        <p>{student_name} 학생은 덕이고에서 {total_books}권의 책을 읽었습니다 📚</p>
                        <p>{student_name} 학생의 독서 기록은 상위 {percentile_text}입니다 🏅</p>
                        <p>{student_name} 학생이 <strong>가장 많이 읽은 분야는 {most_read_category}입니다 📖</strong></p>
                    </div>
                    
                    <div class="book-grid">
    """

    for idx, row in df_merged.iterrows():
        thumbnail = row.get('thumbnail', 'https://via.placeholder.com/100x150.png?text=No+Image')
        title = row.get('제목') or '제목 없음'
        date_raw = row.get('대출일') or '정보 없음'
        date = date_raw + '.' if date_raw != '정보 없음' else date_raw
        
        print_html += f"""
        <div class="book-item">
            <img src="{thumbnail}" alt="{title}">
            <p>{title}<br>{date}</p>
        </div>
        """

    print_html += """
                    </div>
                </div>
            </div>
        </div>
        
        <script>
        // 폰트 로딩 확인
        document.fonts.ready.then(function() {
            console.log('모든 폰트가 로드되었습니다.');
            document.body.classList.add('fonts-loaded');
        });
        
        // 인쇄 함수
        function printPage() {
            // 폰트가 완전히 로드될 때까지 약간 대기 후 인쇄
            setTimeout(function() {
                window.print();
            }, 500);
        }
        
        // 문서 제목 설정
        document.title = '독서 기록 리스트';
        </script>
    </body>
    </html>
    """
    return print_html

# ----------------------------------------------------
# Streamlit 메인 코드
# ----------------------------------------------------
st.title("독서 기록 ISBN 조회 및 썸네일 표시")

st.markdown("### Kakao API 설정")
try:
    api_key = st.secrets["kakao"]["api_key"]
    api_url = st.secrets["kakao"].get("api_url", "https://dapi.kakao.com/v3/search/book")
except KeyError:
    st.error("Kakao API key가 설정되어 있지 않습니다. `.streamlit/secrets.toml` 파일 또는 Streamlit Cloud의 Secrets 설정을 확인하세요.")
    st.stop()

headers = {"Authorization": f"KakaoAK {api_key}"}

st.markdown("## 1. 대출 기록")
main_file = st.file_uploader("독서 기록 파일 업로드", type=["xlsx", "xls"], key="main")

st.markdown("## 2. 전체 소장 도서")
mapping_file = st.file_uploader("등록번호-ISBN 매핑 파일 업로드", type=["xlsx", "xls"], key="mapping")

if main_file is not None and mapping_file is not None:
    try:
        # -------------------------
        # (1) 파일 한번에 읽기
        # -------------------------
        df_all = pd.read_excel(main_file, header=None)
        text_in_4th_row = df_all.iloc[3, 0]

        pattern_name = r"성명\s*:\s*(.+)"
        match_name = re.search(pattern_name, text_in_4th_row)
        student_name = match_name.group(1).strip() if match_name else "이름 미상"

        pattern_class = r"학년\s*-\s*반\s*-\s*번호\s*:\s*([0-9]+)-([0-9]+)-([0-9]+)"
        match_class = re.search(pattern_class, text_in_4th_row)
        class_info = f"{match_class.group(1)}학년 {match_class.group(2)}반 {match_class.group(3)}번" if match_class else "학번 미상"
        grade_detected = int(match_class.group(1)) if match_class else None

        st.write(f"**학생 이름:** {student_name}")
        st.write(f"**학번 정보:** {class_info}")

        if grade_detected:
            st.write(f"**자동 판독된 학년:** {grade_detected}학년")

        # -------------------------
        # (2) 독서 기록 처리 - 청구기호 열 추가 (G열)
        # -------------------------
        df_main = df_all.iloc[5:, [1, 2, 6, 8]]  # B열(등록번호), C열(제목), G열(청구기호), I열(대출일)
        df_main.columns = ['등록번호', '제목', 'call_number', '대출일']
        df_main.dropna(subset=['등록번호'], inplace=True)
        df_main['등록번호'] = df_main['등록번호'].astype(str).str.strip()
        df_main = df_main[df_main['등록번호'] != '']
        df_main['대출일'] = pd.to_datetime(df_main['대출일'], errors='coerce').dt.strftime('%Y.%m.%d')

        # -------------------------
        # (3) 매핑 파일 처리
        # -------------------------
        df_mapping = pd.read_excel(mapping_file, usecols=['등록번호', 'ISBN'])
        df_mapping.columns = df_mapping.columns.str.strip()
        if '등록 번호' in df_mapping.columns:
            df_mapping.rename(columns={'등록 번호': '등록번호'}, inplace=True)
        df_mapping['등록번호'] = df_mapping['등록번호'].astype(str).str.strip()
        df_mapping['ISBN'] = df_mapping['ISBN'].astype(str).str.strip()

        # -------------------------
        # (4) 병합 및 썸네일 캐싱
        # -------------------------
        df_merged = pd.merge(df_main, df_mapping, on='등록번호', how='left')
        df_merged['thumbnail'] = None

        # -------------------------
        # (5) 독서 분야 분석
        # -------------------------
        most_read_category, category_stats = find_most_read_category(df_merged)

        df_merged = fetch_all_thumbnails(df_merged, headers, api_url)
        processed_count = df_merged['thumbnail'].notna().sum()

        st.success(f"🎉 총 {processed_count}건 처리 완료! 🎉")

        # -------------------------
        # (6) 인쇄용 HTML - 독서 분야 정보 추가
        # -------------------------
        if st.button("인쇄용 페이지 생성"):
            print_view = generate_print_view(
                df_merged, 
                student_name, 
                processed_count, 
                grade_detected, 
                most_read_category, 
                category_stats
            )
            components.html(print_view, height=600, scrolling=True)

    except Exception as e:
        st.error(f"파일 처리 중 오류가 발생했습니다: {e}")
