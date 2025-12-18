import streamlit as st
from openai import OpenAI
from tavily import TavilyClient
import arxiv
from firecrawl import FirecrawlApp
import datetime
import json

# --------------------------------------------------------------------------
# 1. 페이지 및 기본 설정
# --------------------------------------------------------------------------
st.set_page_config(
    page_title="최신 지식 검색 챗봇 (GPT-4o mini)",
    page_icon="🤖",
    layout="wide"
)

# API 키 로드
try:
    OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]
    TAVILY_API_KEY = st.secrets["TAVILY_API_KEY"]
    FIRECRAWL_API_KEY = st.secrets["FIRECRAWL_API_KEY"]
except FileNotFoundError:
    st.error("API 키가 설정되지 않았습니다. secrets.toml 파일을 확인해주세요.")
    st.stop()

# 클라이언트 초기화
client = OpenAI(api_key=OPENAI_API_KEY)
tavily_client = TavilyClient(api_key=TAVILY_API_KEY)
firecrawl_app = FirecrawlApp(api_key=FIRECRAWL_API_KEY)

# --------------------------------------------------------------------------
# 2. 실제 Python 함수 정의
# --------------------------------------------------------------------------

def get_current_time():
    """현재 날짜와 시간을 반환"""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def tavily_search_tool(query):
    """Tavily 검색 수행"""
    try:
        response = tavily_client.search(query=query, search_depth="advanced")
        return json.dumps(response.get('results', [])[:3], ensure_ascii=False)
    except Exception as e:
        return f"Error: {str(e)}"

def arxiv_search_tool(query):
    """ArXiv 논문 검색 수행"""
    try:
        search = arxiv.Search(query=query, max_results=3, sort_by=arxiv.SortCriterion.Relevance)
        results = []
        for result in search.results():
            results.append({
                "title": result.title,
                "summary": result.summary[:200] + "...",
                "published": result.published.strftime("%Y-%m-%d"),
                "pdf_url": result.pdf_url
            })
        return json.dumps(results, ensure_ascii=False)
    except Exception as e:
        return f"Error: {str(e)}"

from urllib.parse import urlparse

def normalize_url(url):
    """URL 비교를 위해 http/https, www 등을 제거하고 표준화"""
    try:
        parsed = urlparse(url)
        # 도메인과 경로만 남김 (예: m.etnews.com/123 -> etnews.com/123)
        netloc = parsed.netloc.replace('www.', '').replace('m.', '')
        return f"{netloc}{parsed.path}"
    except:
        return url

def firecrawl_scrape_tool(url):
    """
    URL 스크래핑 도구 (환각 방지 강화판)
    1. Firecrawl 직접 스크래핑 시도
    2. 실패 시 Tavily 검색하되, 'URL이 정확히 일치하는 결과'만 가져옴
    3. 일치하는 게 없으면 '내용 없음'으로 종료 (다른 기사 가져오기 금지)
    """
    # 1단계: Firecrawl 직접 스크래핑
    try:
        print(f"🕵️‍♂️ Firecrawl 스크래핑 시도: {url}")
        if hasattr(firecrawl_app, 'scrape_url'):
            scrape_result = firecrawl_app.scrape_url(url, params={'formats': ['markdown']})
        elif hasattr(firecrawl_app, 'scrape'):
            scrape_result = firecrawl_app.scrape(url, params={'formats': ['markdown']})
        else:
            raise AttributeError("Firecrawl 메소드 확인 필요")

        # 결과 파싱
        content = ""
        if isinstance(scrape_result, dict):
            content = scrape_result.get('markdown', "")
            if not content and 'data' in scrape_result:
                 content = scrape_result['data'].get('markdown', "")
        else:
            content = str(scrape_result)
        
        # 100자 미만이면 차단/실패로 간주
        if not content or len(content) < 100:
            raise Exception("직접 접속 차단됨")
            
        return json.dumps({
            "status": "success", 
            "method": "direct_scrape", 
            "content": content[:4000]
        }, ensure_ascii=False)

    # 2단계: Tavily 검색 우회 (엄격 검증)
    except Exception as e:
        print(f"⚠️ 직접 접속 실패. 검색 우회 시도 중...")
        
        try:
            # Tavily에 URL 검색
            fallback_result = tavily_client.search(query=url, search_depth="advanced")
            raw_results = fallback_result.get('results', [])
            
            # [핵심 수정] 검색 결과 중 '입력한 URL'과 같은 주소를 가진 것만 찾음
            matched_content = []
            target_norm = normalize_url(url)
            
            for res in raw_results:
                res_norm = normalize_url(res.get('url', ''))
                # URL이 거의 일치하는 경우에만 신뢰
                if target_norm in res_norm or res_norm in target_norm:
                    matched_content.append(f"- 제목: {res.get('title')}\n- 내용: {res.get('content')}")
            
            if not matched_content:
                # 일치하는 URL이 검색 결과에 없으면 과감히 포기 (환각 방지)
                return json.dumps({
                    "status": "error", 
                    "message": "페이지 내용을 읽을 수 없습니다. (스크래핑 차단 및 검색 캐시 없음). 거짓 정보를 드리지 않기 위해 답변을 중단합니다."
                }, ensure_ascii=False)

            return json.dumps({
                "status": "fallback_success", 
                "method": "verified_search_cache",
                "content": "\n\n".join(matched_content)
            }, ensure_ascii=False)
            
        except Exception as tavily_e:
            return json.dumps({"status": "error", "message": "정보 수집 완전 실패"}, ensure_ascii=False)

    # 2단계: 실패 시 Tavily 검색으로 우회 (Fallback)
    except Exception as e:
        print(f"⚠️ 스크래핑 실패 ({str(e)}). Tavily 우회 검색을 시작합니다.")
        
        try:
            # URL 자체를 검색어로 입력하여 검색 엔진이 알고 있는 정보를 요청
            # 'search_depth="advanced"'를 써야 좀 더 상세한 정보를 가져옵니다.
            fallback_result = tavily_client.search(query=url, search_depth="advanced")
            
            # 검색 결과에서 가장 관련성 높은 텍스트 추출
            results = fallback_result.get('results', [])
            
            if not results:
                return json.dumps({"status": "error", "message": "스크래핑 및 검색 우회 모두 실패했습니다."}, ensure_ascii=False)

            # 검색된 정보들을 모아서 반환
            fallback_content = []
            for res in results[:2]: # 상위 2개 결과만 참조
                fallback_content.append(f"- 제목: {res.get('title')}\n- 내용요약: {res.get('content')}\n- 링크: {res.get('url')}")
            
            joined_content = "\n\n".join(fallback_content)

            return json.dumps({
                "status": "fallback_success", 
                "method": "tavily_search_fallback",
                "note": "직접 접속이 차단되어 검색 엔진의 요약 정보를 대신 제공합니다. 전체 전문이 아닐 수 있습니다.",
                "content": joined_content
            }, ensure_ascii=False)
            
        except Exception as tavily_e:
            return json.dumps({
                "status": "error", 
                "message": f"모든 수집 시도 실패. Firecrawl: {str(e)}, Tavily: {str(tavily_e)}"
            }, ensure_ascii=False)
        

# 함수 매핑 (문자열 이름으로 실제 함수를 찾기 위함)
available_functions = {
    "get_current_time": get_current_time,
    "tavily_search_tool": tavily_search_tool,
    "arxiv_search_tool": arxiv_search_tool,
    "firecrawl_scrape_tool": firecrawl_scrape_tool,
}

# --------------------------------------------------------------------------
# 3. OpenAI용 도구 스키마 정의 (JSON Schema)
# --------------------------------------------------------------------------
tools = [
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "현재 날짜와 시간을 확인합니다.",
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tavily_search_tool",
            "description": "최신 뉴스, 트렌드, 일반 웹 정보를 검색합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "검색할 키워드 또는 질문"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "arxiv_search_tool",
            "description": "학술 논문 및 연구 자료를 검색합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "논문 주제 또는 키워드"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "firecrawl_scrape_tool",
            "description": "특정 웹페이지 URL의 내용을 상세 분석합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "분석할 웹페이지 URL"}
                },
                "required": ["url"]
            }
        }
    }
]

# 시스템 프롬프트 (환각 방지 지침 추가)
SYSTEM_MESSAGE = {
    "role": "system",
    "content": """
    당신은 '리서치 전문 Agent'입니다. GPT-4o mini 모델을 기반으로 작동합니다.
    질문에 대해 주어진 도구를 적극적으로 활용하여 정확한 정보를 제공하세요.
    
    [중요: 환각 방지 지침]
    1. 도구(Firecrawl, Tavily 등) 실행 결과가 "오류"이거나 "내용 없음"일 경우, 절대로 다른 검색 결과나 사전 지식을 짜깁기하여 해당 URL의 내용인 것처럼 포장하지 마세요.
    2. URL 분석에 실패했다면 "해당 링크의 내용을 확인할 수 없습니다"라고 솔직하게 답변하세요.
    3. 검색 결과가 질문한 URL과 정확히 일치하는 제목/내용이 아니라면 인용하지 마세요.
    
    [답변 형식]
    - 핵심 내용 요약 (두괄식)
    - 상세 설명
    - 출처 및 날짜 명시
    - 관련 링크
        
    답변은 한국어로 작성하세요.
    """
}
# --------------------------------------------------------------------------
# 4. UI 구성 (사이드바 수정됨)
# --------------------------------------------------------------------------
st.markdown("<h1 style='text-align: center; color: #4A90E2;'>최신 지식 검색 챗봇</h1>", unsafe_allow_html=True)
st.markdown("<div style='text-align: center; color: #666;'>Powered by GPT-4o mini</div><hr>", unsafe_allow_html=True)

with st.sidebar:
    # 1. 초기화 버튼
    if st.button("새로운 대화 시작", type="primary", use_container_width=True):
        st.session_state.messages = [SYSTEM_MESSAGE]
        st.rerun()
    
    st.markdown("---")

    # 1-1. 개발자 정보
    developer_info = """
    <div style='background-color: #f0f2f6; padding: 15px; border-radius: 10px; border: 1px solid #ddd;'>
        <h4 style='margin-top:0; color: #333; font-size: 16px;'>👨‍💻 개발자 정보</h4>
        <p style='margin-bottom: 5px; font-size: 14px;'><strong>Name:</strong> Prof. LCH</p>
        <p style='margin-bottom: 0; font-size: 14px;'><strong>Email:</strong> <a href='mailto:leesleek@ginue.ac.kr' style='text-decoration: none; color: #4A90E2;'>leesleek@ginue.ac.kr</a></p>
    </div>
    """
    st.markdown(developer_info, unsafe_allow_html=True)
    
    # 2. 챗봇 기능 소개 (요청하신 내용 적용)
    st.subheader("🤖 기능 및 사용법")
    
    introduction = """
    저는 다양한 질문에 대한 답변을 제공하고, 정보를 검색하여 요약하거나 설명하는 작업을 수행할 수 있습니다. 
    구체적으로는 다음과 같은 기능을 가지고 있습니다:
    """
    st.markdown(introduction)
    
    # 기능 1: 정보 검색
    st.markdown("#### 🌐 정보 검색")
    st.markdown("최신 뉴스, 트렌드, 일반 웹 정보를 검색하고 요약합니다.")
    st.info("예시: 2025년 최신 AI 기술 트렌드 알려줘")

    # 기능 2: 학술 자료 검색
    st.markdown("#### 📚 학술 자료 검색")
    st.markdown("최신 연구 논문 및 학술 자료를 검색합니다.")
    st.info("예시: LLM 환각(Hallucination) 해결 관련 논문 찾아줘")

    # 기능 3: URL 분석
    st.markdown("#### 🔥 URL 분석")
    st.markdown("특정 웹페이지의 내용을 분석하여 상세 정보를 제공합니다.")
    st.info("예시: https://www.etnews.com/... 이 기사 내용 요약해줘")

    # 기능 4: 질문 답변
    st.markdown("#### 💬 질문 답변")
    st.markdown("다양한 분야에 대한 질문에 대한 답변을 제공합니다.")
    st.info("예시: 파이썬에서 리스트와 튜플의 차이점이 뭐야?")

    st.markdown("---")
    st.caption("이 외에도 추가 질문이나 특정 요청이 있으시면 말씀해 주세요!")
    
    st.markdown("---")
    
    # 3. 개발자 정보
    developer_info = """
    <div style='background-color: #f0f2f6; padding: 15px; border-radius: 10px; border: 1px solid #ddd;'>
        <h4 style='margin-top:0; color: #333; font-size: 16px;'>👨‍💻 개발자 정보</h4>
        <p style='margin-bottom: 5px; font-size: 14px;'><strong>Name:</strong> 이철현(경인교육대학교)</p>
        <p style='margin-bottom: 0; font-size: 14px;'><strong>Email:</strong> <a href='mailto:leesleek@ginue.ac.kr' style='text-decoration: none; color: #4A90E2;'>leesleek@ginue.ac.kr</a></p>
    </div>
    """
    st.markdown(developer_info, unsafe_allow_html=True)

# --------------------------------------------------------------------------
# 5. 챗봇 로직 (OpenAI 방식 + 도구 목록 표시 기능 추가)
# --------------------------------------------------------------------------

# 도구 이름 매핑 (사용자 친화적인 이름)
tool_name_map = {
    "tavily_search_tool": "🌐 Tavily Search (웹 검색)",
    "arxiv_search_tool": "📚 ArXiv Search (논문 검색)",
    "firecrawl_scrape_tool": "🔥 Firecrawl Scrape (웹 상세분석)",
    "get_current_time": "⏰ Current Time (시간 확인)"
}

# 세션 초기화
if "messages" not in st.session_state:
    st.session_state.messages = [SYSTEM_MESSAGE]

# 대화 기록 표시
# --------------------------------------------------------------------------
for message in st.session_state.messages:
    # 1. 메시지 타입에 따라 데이터 추출 (Dict 또는 Object 대응)
    if isinstance(message, dict):
        role = message["role"]
        content = message.get("content")
    else:
        # OpenAI 객체(ChatCompletionMessage)인 경우 속성(Attribute)으로 접근
        role = message.role
        content = message.content

    # 2. 화면에 표시 (System 메시지 제외, 내용이 있는 경우만)
    if role != "system" and content:
        with st.chat_message(role):
            st.markdown(content)

# 사용자 입력 처리
if prompt := st.chat_input("질문을 입력하세요..."):
    # 사용자 메시지 추가
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # AI 응답 처리
    with st.chat_message("assistant"):
        message_placeholder = st.empty()
        
        try:
            # 1차 호출: 모델이 도구를 쓸지 말지 결정
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=st.session_state.messages,
                tools=tools,
                tool_choice="auto" 
            )
            
            response_message = response.choices[0].message
            tool_calls = response_message.tool_calls

            # 도구 호출이 있는 경우
            if tool_calls:
                st.session_state.messages.append(response_message)
                
                # 사용된 도구 이름을 저장할 리스트
                used_tools_display = []

                with st.spinner("도구를 사용하여 정보를 수집 중입니다..."):
                    for tool_call in tool_calls:
                        function_name = tool_call.function.name
                        function_to_call = available_functions[function_name]
                        function_args = json.loads(tool_call.function.arguments)
                        
                        # 도구 이름 저장 (매핑된 이름이 없으면 원래 함수명 사용)
                        friendly_name = tool_name_map.get(function_name, function_name)
                        if friendly_name not in used_tools_display:
                            used_tools_display.append(friendly_name)

                        # 실제 함수 실행
                        if function_name == "get_current_time":
                            function_response = function_to_call()
                        else:
                            function_response = function_to_call(**function_args)
                        
                        # 실행 결과 메시지 추가
                        st.session_state.messages.append({
                            "tool_call_id": tool_call.id,
                            "role": "tool",
                            "name": function_name,
                            "content": function_response,
                        })

                    # 2차 호출: 도구 결과를 포함하여 최종 답변 생성
                    final_response = client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=st.session_state.messages
                    )
                    final_content = final_response.choices[0].message.content
                    
                    # [추가됨] 답변 하단에 사용된 도구 목록 붙이기
                    if used_tools_display:
                        tools_str = ", ".join(used_tools_display)
                        # Markdown 인용구 스타일이나 굵은 글씨로 구분
                        final_content += f"\n\n---\n**🛠 사용된 도구:** {tools_str}"

                    message_placeholder.markdown(final_content)
                    st.session_state.messages.append({"role": "assistant", "content": final_content})
            
            # 도구 호출이 없는 경우
            else:
                final_content = response_message.content
                message_placeholder.markdown(final_content)
                st.session_state.messages.append({"role": "assistant", "content": final_content})

        except Exception as e:
            st.error(f"오류 발생: {e}")