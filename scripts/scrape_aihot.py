import requests
from bs4 import BeautifulSoup
import json
import re
import os
from datetime import datetime, timedelta
import concurrent.futures
import trafilatura
from collections import defaultdict
import feedparser
import time

# Cache configuration
CONTENT_CACHE_FILE = 'article_content_cache.json'
global_content_cache = {}

def load_content_cache():
    """Load content cache from disk."""
    global global_content_cache
    if os.path.exists(CONTENT_CACHE_FILE):
        try:
            with open(CONTENT_CACHE_FILE, 'r', encoding='utf-8') as f:
                global_content_cache = json.load(f)
            print(f"Loaded {len(global_content_cache)} items from content cache.")
        except Exception as e:
            print(f"Error loading content cache: {e}")
            global_content_cache = {}

def save_content_cache():
    """Save content cache to disk."""
    try:
        with open(CONTENT_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(global_content_cache, f, ensure_ascii=False, indent=2)
        print("Content cache saved.")
    except Exception as e:
        print(f"Error saving content cache: {e}")

# AI Backend Configuration
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AI_API_KEY_FILE = os.path.join(PROJECT_DIR, ".config", "openrouter_api_key.txt")

def load_ai_api_key():
    try:
        with open(AI_API_KEY_FILE, 'r', encoding='utf-8') as f:
            key = f.read().strip()
    except FileNotFoundError:
        return ""

    if key == "PASTE_OPENROUTER_API_KEY_HERE":
        return ""
    return key

AI_API_KEY = load_ai_api_key()
AI_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
AI_MODEL_NAME = "google/gemini-3-flash-preview"
#AI_MODEL_NAME = "google/gemini-3-pro-preview" 

# Global Headers
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'application/rss+xml,application/xml,text/xml,text/html,*/*'
}

# Feature Flags
ENABLE_RSS = os.getenv('ENABLE_RSS', 'true').lower() == 'true'
RSS_CONFIG_REL_PATH = "../resources/content_curator_sources.json"
MAX_LOOKBACK_DAYS = 30
GENERAL_LOOKBACK_DAYS = 14
RECENT_PRIORITY_DAYS = 3
SELECTION_COUNT = 3

def truncate_text(text, max_len=800):
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    
    # User requested: first 400 chars + middle 400 chars with ellipsis
    part1 = text[:400]
    
    # Calculate middle part start
    # Ensure it doesn't overlap with part1 (start >= 400)
    # And has enough space for 400 chars (though we slice safely)
    mid_center = len(text) // 2
    start2 = max(400, mid_center - 200)
    end2 = start2 + 400
    
    # Safety clamp
    if end2 > len(text):
        end2 = len(text)
        start2 = max(400, end2 - 400)
        
    part2 = text[start2:end2]
    
    return f"{part1}\n......\n{part2}"

def parse_date_string(date_str):
    """
    Parse date string in various formats including YYYY-MM-DD and YYYY年MM月DD日.
    Returns datetime object or None.
    """
    if not date_str or not isinstance(date_str, str):
        return None
    
    date_str = date_str.strip()
    
    # Try YYYY-MM-DD
    try:
        return datetime.strptime(date_str, '%Y-%m-%d')
    except ValueError:
        pass
        
    # Try YYYY年MM月DD日
    try:
        return datetime.strptime(date_str, '%Y年%m月%d日')
    except ValueError:
        pass
        
    # Try YYYY/MM/DD
    try:
        return datetime.strptime(date_str, '%Y/%m/%d')
    except ValueError:
        pass

    return None

def is_old(date_str_or_obj, days_limit=MAX_LOOKBACK_DAYS):
    """
    Check if the date is older than days_limit days from today.
    Accepts string (YYYY-MM-DD, YYYY年MM月DD日) or datetime object.
    Returns True if old, False if recent. 
    IMPORTANT: Returns True (skip) if date cannot be parsed (safety).
    """
    if not date_str_or_obj:
        return True # Treat missing date as "old" (skip it)
    
    today = datetime.now()
    dt = None
    
    try:
        if isinstance(date_str_or_obj, datetime):
            dt = date_str_or_obj
        else:
            dt = parse_date_string(str(date_str_or_obj))
            
        if dt:
            delta = today - dt
            # Allow future dates (e.g. timezones) but filter old ones
            return delta.days > days_limit
        else:
            return True # Could not parse, so skip
            
    except Exception:
        return True
    
    return True # Should not reach here if dt is valid, but default to safe skip

def load_rss_config():
    """Load RSS sources configuration."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, RSS_CONFIG_REL_PATH)
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Config file not found at: {config_path}")
        return {"sources": []}
    except Exception as e:
        print(f"Error loading config: {e}")
        return {"sources": []}

def fetch_single_rss_source(url, category):
    """Fetch and parse a single RSS feed."""
    items = []
    try:
        print(f"Fetching RSS: {url}")
        # Use requests with strict timeout instead of feedparser's internal fetcher
        try:
            response = requests.get(url, headers=HEADERS, timeout=15)
            response.raise_for_status()
            feed = feedparser.parse(response.content)
        except Exception as e:
            print(f"Error fetching RSS content {url}: {e}")
            return []

        if hasattr(feed, 'status') and feed.status != 200 and feed.status != 301 and feed.status != 302:
                pass

        for entry in feed.entries[:10]: # Limit to 10 per feed
            # Extract date
            published_date = None
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    published_date = datetime.fromtimestamp(time.mktime(entry.published_parsed))
            elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
                    published_date = datetime.fromtimestamp(time.mktime(entry.updated_parsed))
            
            date_str = published_date.strftime('%Y-%m-%d') if published_date else ""

            items.append({
                "title": entry.get('title', ''),
                "description": entry.get('summary', ''),
                "link": entry.get('link', ''),
                "platform": f"{category} (RSS)",
                "article_date": date_str,
                "stats": "",
                "source_type": "RSS"
            })
    except Exception as e:
        print(f"Error parsing RSS {url}: {e}")
    return items

def fetch_configured_sources(config):
    """Fetch items from configured sources (RSS and Web) in parallel."""
    items = []
    sources = config.get('sources', [])
    
    print(f"Processing {len(sources)} configured sources...")
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = []
        
        for source in sources:
            stype = source.get('type')
            urls = source.get('urls', [])
            category = source.get('category', 'Uncategorized')
            
            if stype == 'RSS':
                for url in urls:
                    futures.append(executor.submit(fetch_single_rss_source, url, category))
            else:
                # Assume Web/Direct links - add directly
                for url in urls:
                    items.append({
                        "title": "", # Will be extracted
                        "description": "",
                        "link": url,
                        "platform": f"{category} (Web)",
                        "article_date": "",
                        "stats": "",
                        "source_type": "Web"
                    })
        
        # Collect RSS results
        for future in concurrent.futures.as_completed(futures):
            try:
                feed_items = future.result()
                items.extend(feed_items)
            except Exception as e:
                print(f"Error in RSS worker: {e}")
                
    return items

MAX_ARTICLE_BYTES = 2 * 1024 * 1024
ARTICLE_TOTAL_TIMEOUT = 25

def download_article_html(url):
    deadline = time.monotonic() + ARTICLE_TOTAL_TIMEOUT
    chunks = []
    total_bytes = 0

    with requests.get(url, headers=HEADERS, timeout=(5, 10), stream=True) as response:
        response.raise_for_status()
        encoding = response.encoding or 'utf-8'

        for chunk in response.iter_content(chunk_size=64 * 1024):
            if time.monotonic() > deadline:
                raise requests.Timeout(f"Total download time exceeded {ARTICLE_TOTAL_TIMEOUT}s")
            if not chunk:
                continue

            remaining = MAX_ARTICLE_BYTES - total_bytes
            if remaining <= 0:
                break
            chunks.append(chunk[:remaining])
            total_bytes += min(len(chunk), remaining)
            if total_bytes >= MAX_ARTICLE_BYTES:
                break

    return b''.join(chunks).decode(encoding, errors='replace')

def fetch_article_content(item):
    """
    Fetch the main content and date from the item's link using trafilatura.
    Returns the modified item dict.
    """
    url = item.get('link')
    if not url:
        return item
        
    # Check cache first
    if url in global_content_cache:
        cached_data = global_content_cache[url]
        # Only use cache if it has content (avoid caching failures permanently unless we want to)
        # But if we cached an empty string, maybe it really was empty.
        # Let's assume cache is valid.
        item['content'] = cached_data.get('content', '')
        item['article_date'] = cached_data.get('article_date', '')
        if cached_data.get('title'):
             item['title'] = cached_data.get('title')
        # print(f"Cache hit for {url}")
        return item

    try:
        try:
            downloaded = download_article_html(url)
        except Exception as e:
            # print(f"Error downloading {url}: {e}")
            item['content_error'] = str(e)
            return item
        
        if downloaded:
            # Extract content
            result = trafilatura.extract(downloaded, 
                                         include_comments=False, 
                                         include_tables=True,
                                         date_extraction_params={'extensive_search': True})
            
            if result:
                # Truncate content here
                item['content'] = truncate_text(result, 800)
            else:
                item['content'] = "" # Mark as empty
                
            # Extract metadata
            metadata = trafilatura.bare_extraction(downloaded, 
                                                   include_comments=False, 
                                                   include_tables=True,
                                                   date_extraction_params={'extensive_search': True})
            
            if metadata:
                if hasattr(metadata, 'as_dict'):
                    meta_dict = metadata.as_dict()
                else:
                    meta_dict = metadata if isinstance(metadata, dict) else {}

                if meta_dict.get('date'):
                    # Only overwrite if we don't have a date or the new date looks valid
                    # IMPORTANT: Trafilatura sometimes picks up 'updated' date from footer or sidebar.
                    # We should be careful. If we already have a date from description (which is often more reliable for aggregators),
                    # we might want to prefer that if trafilatura's date is "today" (implying fetch date/dynamic date).
                    
                    new_date = meta_dict['date']
                    existing_date = item.get('article_date')
                    
                    # If we already have a date and trafilatura returns today's date, it's suspicious (likely 'page generated' time)
                    if existing_date and new_date == datetime.now().strftime('%Y-%m-%d'):
                         pass # Keep existing date
                    else:
                         item['article_date'] = new_date
                
                # If we still don't have a date, try to find one in the text using regex (fallback)
                if not item.get('article_date'):
                     # Try to extract from URL first (often reliable)
                     # Matches /2024/01/01/ or /2024-01-01/
                     url_match = re.search(r'/(\d{4})[/-](\d{2})[/-](\d{2})/', url)
                     if url_match:
                         item['article_date'] = f"{url_match.group(1)}-{url_match.group(2)}-{url_match.group(3)}"
                     else:
                         # Simple regex for YYYY-MM-DD in start of content (sometimes helps)
                         match = re.search(r'(\d{4}-\d{2}-\d{2})', item.get('content', '')[:500])
                         if match:
                             item['article_date'] = match.group(1)

                if meta_dict.get('title') and not item.get('title'):
                    item['title'] = meta_dict['title']
                # Fallback content
                if not item.get('content') and meta_dict.get('text'):
                    item['content'] = truncate_text(meta_dict['text'], 800)

    except Exception as e:
        print(f"Error fetching {url}: {e}")
        item['content_error'] = str(e)
    
    # Final fallback: Use RSS description if content is still empty
    if not item.get('content') and item.get('description'):
        item['content'] = truncate_text(item.get('description'), 800)
        
    return item

def generate_html_report(items, output_dir):
    """Generate a beautiful HTML report for the selected items."""
    
    html_template = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Hot Picks - {date}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background-color: #f8f9fa;
        }}
        .card {{
            transition: transform 0.2s;
        }}
        .card:hover {{
            transform: translateY(-2px);
        }}
    </style>
</head>
<body class="bg-gray-100 min-h-screen py-10 px-4 sm:px-6 lg:px-8">
    <div class="max-w-5xl mx-auto">
        <header class="mb-10 text-center">
            <h1 class="text-4xl font-bold text-gray-900 mb-2">AI 热点精选</h1>
            <p class="text-gray-500">{date} • Generated by AIHot</p>
        </header>
        
        <div class="space-y-6">
            {content}
        </div>
        
        <footer class="mt-12 text-center text-gray-400 text-sm">
            <p>Generated by AIHot Scraper</p>
        </footer>
    </div>
</body>
</html>
"""
    
    item_template = """
            <div class="bg-white rounded-lg shadow-md overflow-hidden card hover:shadow-lg border border-gray-100">
                <div class="p-6">
                    <div class="flex items-center justify-between mb-2">
                        <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800">
                            {platform}
                        </span>
                        <span class="text-gray-400 text-sm">{date}</span>
                    </div>
                    <a href="{link}" target="_blank" class="block mt-2">
                        <h2 class="text-xl font-semibold text-gray-900 hover:text-blue-600 transition-colors">{title_zh}</h2>
                    </a>
                    <div class="mt-2 text-sm text-gray-500">
                         原文标题: {original_title}
                    </div>
                    <div class="mt-4 p-4 bg-gray-50 rounded-md">
                        <p class="text-gray-700 font-medium">推荐理由：</p>
                        <p class="text-gray-600 mt-1">{reason}</p>
                    </div>
                    <div class="mt-4 flex justify-end">
                        <a href="{link}" target="_blank" class="text-blue-600 hover:text-blue-800 text-sm font-medium flex items-center">
                            阅读原文
                            <svg class="ml-1 w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"></path>
                            </svg>
                        </a>
                    </div>
                </div>
            </div>
"""
    
    content_html = ""
    for item in items:
        content_html += item_template.format(
            platform=item.get('platform', 'Unknown'),
            date=item.get('article_date', 'Unknown'),
            link=item.get('link', '#'),
            title_zh=item.get('title_zh', item.get('title', 'No Title')),
            original_title=item.get('title', ''),
            reason=item.get('reason', 'No reason provided.')
        )
        
    final_html = html_template.format(
        date=datetime.now().strftime('%Y-%m-%d %H:%M'),
        content=content_html
    )
    
    output_path = os.path.join(output_dir, 'index.html')
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(final_html)
        print(f"HTML report generated at: {output_path}")
    except Exception as e:
        print(f"Error generating HTML report: {e}")

# 选题标准：反向拆解自对标账号两篇样本（2026-09-14、2026-09-15）。
# 样本共性：一天一篇、一篇只讲一件事、有一手材料与硬数字、落到产业竞争格局和「你该怎么办」。
READER = """目标读者：
- 企业管理者、IT 与数字化负责人（CIO、信息化、数据、财务条线），以及管理咨询顾问和企业数字化规划从业者。
- 关注 AI 与产业变化如何影响企业投入、组织和个人职业的决策者。
- 他们要的不是「发生了什么」，而是「为什么会这样、对企业和个人意味着什么、现在该做什么」。"""

EXCLUSIONS = [
    "多事件合集：今日热点、周报、月度盘点、榜单搬运。",
    "纯模型参数、跑分、论文细节、开源项目小版本更新等与决策无关的技术内容。",
    "只有融资数字、没有商业逻辑拆解的快讯。",
    "企业通稿、发布会通稿改写、缺少数据支撑的观点或预测。",
    "缺少独立判断的翻译稿，以及标题党。",
]

# 切换方式：环境变量 CONTENT_FOCUS，或 resources/content_curator_sources.json 的 focus 字段。
FOCUS_PROFILES = {
    "consulting": {
        "label": "企业数字化与 IT 规划",
        "priorities": [
            "单一事件深挖：官方博客、财报、发布会、权威调研里的一手材料，能引用具体数字、时间点和金额。",
            "企业 IT 投入与数字化规划：IT 预算怎么花、系统替换或重构（ERP、数据平台、中台）、自建还是采购、AI 项目的成本与 ROI。",
            "AI 在企业里的真实落地：哪个环节被替代、组织和岗位怎么变、哪里踩坑，要有可验证的细节。",
            "产业竞争格局变化：谁和谁在同一个战场上正面对撞，入口、平台或供给关系怎么迁移。",
            "有反常识或强判断的结论，并能落到企业该怎么投入、个人该补什么能力。",
        ],
    },
    "finance": {
        "label": "财务与商业",
        "priorities": [
            "单一事件深挖：财报、招股书、并购公告、监管文件里的一手材料，能引用具体数字与时间点。",
            "财务数字化：司库、预算与预测、财务共享中心、费用与税务流程的自动化，以及 AI 在财务职能里的真实落地与成本变化。",
            "商业模式与单位经济：定价、毛利结构、现金流、增长与衰退背后的机制，而不是只讲涨跌。",
            "资本市场与金融科技：支付、借贷、财富管理、稳定币的竞争与监管变化，以及融资、上市、并购背后的判断。",
            "有反常识或强判断的结论，并能落到企业和个人的资产、职业、投入决策。",
        ],
    },
}
DEFAULT_FOCUS = "consulting"

def load_focus():
    """Focus profile name: env CONTENT_FOCUS > config "focus" > DEFAULT_FOCUS."""
    env_focus = os.getenv('CONTENT_FOCUS', '').strip().lower()
    if env_focus in FOCUS_PROFILES:
        return env_focus
    config_focus = str(load_rss_config().get('focus', '')).strip().lower()
    if config_focus in FOCUS_PROFILES:
        return config_focus
    return DEFAULT_FOCUS

def call_ai_selection(items, top_n=SELECTION_COUNT):
    """
    Call AI to select top N items.
    """
    print(f"Calling AI to select top {top_n} items from {len(items)} candidates...")
    
    # Prepare summary for AI
    candidates_text = ""
    for item in items:
        candidates_text += f"ID: {item['id']}\n"
        candidates_text += f"Title: {item['title']}\n"
        candidates_text += f"Platform: {item['platform']}\n"
        candidates_text += f"Date: {item.get('article_date', '')}\n"
        candidates_text += f"Content: {item['content']}\n"
        candidates_text += "-" * 20 + "\n"
    
    profile = FOCUS_PROFILES[load_focus()]
    print(f"Selection focus: {profile['label']}")
    priorities_block = "\n".join(f"{i}. {p}" for i, p in enumerate(profile['priorities'], 1))
    priorities_block += f"\n{len(profile['priorities']) + 1}. 时效：优先最近 {RECENT_PRIORITY_DAYS} 天内的事件，原则上不超过 {GENERAL_LOOKBACK_DAYS} 天；只有长期影响明显更强时才放宽到 {MAX_LOOKBACK_DAYS} 天。"
    exclusions_block = "\n".join(f"- {e}" for e in EXCLUSIONS)
    
    prompt = f"""
你是一个专业的{profile['label']}热点内容主编。请从以下列表中挑选出前 {top_n} 个最值得做成中文深度文章的选题。

**今天是：{datetime.now().strftime('%Y-%m-%d')}**

**关于文章内容（Content）的特别说明**：
- 为了节省长度，文章内容如果较长，会显示为“开头400字符......中间400字符”的格式。
- **中间的六个省略号（......）代表了文章中间被省略的部分**。
- 请务必阅读 `Content` 字段来判断文章是否有实质性内容（干货），不要仅凭标题判断。如果 Content 为空或看起来是毫无意义的占位符，请直接忽略该文章。

{READER}

优先选题：
{priorities_block}

**排除标准**：
{exclusions_block}

**额外要求**：
- 避免选择重复的文章（不同平台描述同个事务的文章），如有，选择相对内容最全面的那一篇。
- 每篇只讲一件事。一篇能撑起「事件事实 → 机制解释 → 趋势判断 → 你该怎么办」四段的，优先于需要拼盘的。
- {top_n} 个结果应覆盖不同行业或不同角度，但不得为多样性牺牲质量。

请仔细阅读上述内容，挑选出 {top_n} 个 ID。
**输出格式要求**：
- 只返回一个 JSON 数组，包含选中的项目对象。
- 每个对象包含三个字段：
  - "id": 对应文章的ID (整数)
  - "reason": 推荐理由 (中文字符串，简练概括为什么这篇文章值得读)
  - "title_zh": 中文标题 (字符串。**必须结合原标题和正文内容重新生成**，风格参考：直接给判断或反常识提问，如「AI 开始训练 AI，RSI 要来了」「为什么手机才是记忆最好的土壤」；不要用「深度解析」「全面盘点」这类空话，也不要只是翻译原标题。)
- 结果按推荐顺序排序（最推荐的排在前面）。
- 格式示例：[
    {{"id": 12, "reason": "用官方公布的一手数据说明 AI 正在进入研发执行环节，对判断企业该往哪投有直接参考", "title_zh": "AI 开始训练 AI，RSI 要来了"}},
    {{"id": 5, "reason": "把记忆能力讲成了产业竞争的关键变量，能解释手机厂商和互联网巨头为什么会正面撞上", "title_zh": "为什么手机才是记忆最好的土壤？"}}
  ]
- 不要返回任何其他文字。

候选列表：
{candidates_text}
"""

    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/cgerike/aihot", # Optional OpenRouter headers
        "X-Title": "AIHot Scraper"
    }
    
    payload = {
        "model": AI_MODEL_NAME,
        "messages": [
            {"role": "system", "content": "你是一个专业的AI热点选题助手。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7
    }
    
    try:
        print(f"Sending request to {AI_BASE_URL} with model {AI_MODEL_NAME}...")
        response = requests.post(AI_BASE_URL, headers=headers, json=payload, timeout=120)
        
        if response.status_code != 200:
            print(f"API Error: {response.status_code} - {response.text}")
            
        response.raise_for_status()
        result = response.json()
        
        if 'choices' in result and len(result['choices']) > 0:
            content = result['choices'][0]['message']['content']
            print("AI Response received.")
            
            # Clean up markdown code blocks if present
            content = re.sub(r'```json', '', content)
            content = re.sub(r'```', '', content).strip()
            
            try:
                selected_items = json.loads(content)
                if isinstance(selected_items, list):
                    return selected_items
                else:
                    print(f"AI response is not a list: {content[:100]}...")
                    return []
            except json.JSONDecodeError:
                print(f"Failed to parse JSON from AI response: {content[:100]}...")
                return []
        else:
            print(f"Unexpected API response format: {result}")
            return []
            
    except Exception as e:
        print(f"Error calling AI: {e}")
        if 'response' in locals() and response.text:
            print(f"Response body: {response.text}")
        return []

def scrape_aihot():
    if not AI_API_KEY:
        print(f"OpenRouter API Key 未配置，请填写：{AI_API_KEY_FILE}")
        return

    url = 'https://aihot.today/'
    headers = {
        'User-Agent': HEADERS['User-Agent']
    }
    
    ignored_platforms = {
        "Hacker News", "Product Hunt", "极客网", "Hugging Face", 
        "Anthropic", "OpenAI", "Azure AI", "MIT News"
    }
    
    valid_items = []
    
    # Load content cache
    load_content_cache()
    
    try:
        # Step 1: Fetch items from Homepage (Always fetch to get latest links)
        # Try to load from cache first to save traffic
        # Removed old file-based full list cache logic as requested.
        
        print(f"Fetching {url}...")
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        response.encoding = 'utf-8'
        
        soup = BeautifulSoup(response.text, 'html.parser')
        cards = soup.find_all('div', class_=lambda c: c and 'bg-card' in c and 'text-card-foreground' in c)
        
        all_items_to_fetch = []
        
        print(f"Found {len(cards)} cards.")
        
        for card in cards:
            platform_data = {
                "platform": "Unknown",
                "update_time": "",
                "items": []
            }
            
            # Extract Platform Name
            header = card.find('div', class_=lambda c: c and 'flex-row' in c and 'justify-between' in c)
            if header:
                name_span = header.find('span', class_=lambda c: c and 'font-semibold' in c)
                if name_span:
                    platform_data["platform"] = name_span.get_text(strip=True)
                else:
                    img = header.find('img')
                    if img and img.get('alt'):
                        platform_data["platform"] = img.get('alt').replace(' logo', '').strip()
                
                time_span = header.find('span', class_=lambda c: c and 'text-blue-600/80' in c)
                if time_span:
                    platform_data["update_time"] = time_span.get_text(strip=True)
            
            if platform_data["platform"] in ignored_platforms:
                continue
            
            # Extract Items
            list_container = card.find('div', style="min-width:100%;display:table")
            if list_container:
                item_links = list_container.find_all('a')
                for link in item_links:
                    item = {
                        "title": "",
                        "description": "",
                        "link": "",
                        "stats": "",
                        "platform": platform_data["platform"]
                    }
                    
                    raw_link = link.get('href', '')
                    item["link"] = raw_link.replace('.com//', '.com/')
                    
                    title_div = link.find('div', class_='font-[500]')
                    if title_div:
                        raw_title = title_div.get_text(strip=True)
                        item["title"] = re.sub(r'^\d+\s*\.?\s*', '', raw_title)
                    
                    desc_div = link.find('div', class_=lambda c: c and 'text-[#7a7b79]' in c)
                    if desc_div:
                        desc_text = desc_div.get_text(strip=True)
                        # Check if description is actually a date
                        parsed_date = parse_date_string(desc_text)
                        if parsed_date:
                            item["article_date"] = desc_text
                            # If description is just the date, maybe we should clear description or keep it?
                            # Usually better to keep it empty if it's just metadata
                            item["description"] = "" 
                        else:
                            item["description"] = desc_text
                    
                    stats_div = link.find('div', class_=lambda c: c and 'w-[10%]' in c)
                    if stats_div:
                            item["stats"] = stats_div.get_text(strip=True)
                    
                    if item["title"] and item["link"].startswith('http'):
                        all_items_to_fetch.append(item)
        
        # Fetch RSS items
        if ENABLE_RSS:
            print("Fetching configured sources (RSS/Web)...")
            rss_config = load_rss_config()
            rss_items = fetch_configured_sources(rss_config)
            print(f"Fetched {len(rss_items)} items from sources.")
            all_items_to_fetch.extend(rss_items)

        print(f"Found {len(all_items_to_fetch)} items to fetch details for.")
        
        # Group by platform and interleave
        grouped_items = defaultdict(list)
        for item in all_items_to_fetch:
            grouped_items[item['platform']].append(item)
            
        interleaved_items = []
        while grouped_items:
            for platform in list(grouped_items.keys()):
                if grouped_items[platform]:
                    interleaved_items.append(grouped_items[platform].pop(0))
                else:
                    del grouped_items[platform]
        
        # Concurrent fetching
        print("Starting parallel fetch (max 30 workers, interleaved domains)...")
        fetched_items = []
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=30) as executor:
            future_to_item = {executor.submit(fetch_article_content, item): item for item in interleaved_items}
            
            completed_count = 0
            for future in concurrent.futures.as_completed(future_to_item):
                item = future_to_item[future]
                try:
                    future.result() # No timeout needed here as task has internal timeout
                    fetched_items.append(item)
                    completed_count += 1
                    if completed_count % 10 == 0:
                        print(f"Fetched {completed_count}/{len(interleaved_items)}...")
                except Exception as exc:
                    print(f"Exception for {item['link']}: {exc}")

        # Filter items
        for item in fetched_items:
            if not item.get('content') or item['content'].strip() == "":
                continue
            
            # Strict Date Filtering
            a_date = item.get('article_date')
            if not a_date:
                # print(f"Skipping {item.get('title')} - No date found.")
                continue
            
            if is_old(a_date, days_limit=MAX_LOOKBACK_DAYS):
                # print(f"Skipping {item.get('title')} - Date {a_date} is too old.")
                continue
                
            valid_items.append(item)
            
        print(f"Valid items after filtering: {len(valid_items)}")
        
        # Update cache with newly fetched content
        new_cache_entries = 0
        for item in valid_items:
            if item.get('link') and item.get('content'):
                # Only update if not in cache or content changed (simple check)
                if item['link'] not in global_content_cache:
                    global_content_cache[item['link']] = {
                        'title': item.get('title'),
                        'content': item.get('content'),
                        'article_date': item.get('article_date'),
                        'fetched_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    }
                    new_cache_entries += 1
        
        if new_cache_entries > 0:
            print(f"Updating cache with {new_cache_entries} new entries...")
            save_content_cache()

        # Assign IDs
        for idx, item in enumerate(valid_items, 1):
            item['id'] = idx

        # Save valid items just in case AI fails again
        # Note: keeping this for debugging but it's not the main cache anymore
        with open('aihot_valid_all_debug.json', 'w', encoding='utf-8') as f:
            json.dump(valid_items, f, ensure_ascii=False, indent=2)

        # --- FINAL SAFETY CHECK ---
        # Ensure strict filtering applies to both newly fetched AND cached items
        # This fixes issues where cache contains invalid/old/no-date items
        safe_items = []
        for item in valid_items:
            a_date = item.get('article_date')
            if not a_date:
                # print(f"Removing {item.get('title')} - No date (safety check)")
                continue
            
            if is_old(a_date, days_limit=MAX_LOOKBACK_DAYS):
                # print(f"Removing {item.get('title')} - Old date {a_date} (safety check)")
                continue
            safe_items.append(item)
        
        if len(safe_items) < len(valid_items):
            print(f"Removed {len(valid_items) - len(safe_items)} invalid items during final safety check.")
            valid_items = safe_items
            # Update cache with clean data
            # with open('aihot_valid_all.json', 'w', encoding='utf-8') as f:
            #    json.dump(valid_items, f, ensure_ascii=False, indent=2)

        # AI Selection
        if valid_items:
            selected_results = call_ai_selection(valid_items, top_n=SELECTION_COUNT)
            
            if selected_results:
                print(f"AI selected {len(selected_results)} items total.")
                item_map = {item['id']: item for item in valid_items}
                
                final_selection = []
                # Use a set to avoid duplicates if any ID overlap (unlikely but safe)
                seen_ids = set()
                
                for selection in selected_results:
                    if len(final_selection) >= SELECTION_COUNT:
                        break
                    # Handle both integer ID (legacy/fallback) and object (new format)
                    if isinstance(selection, int):
                        sid = selection
                        reason = ""
                        title_zh = ""
                    elif isinstance(selection, dict):
                        sid = selection.get('id')
                        reason = selection.get('reason', "")
                        title_zh = selection.get('title_zh', "")
                    else:
                        continue

                    if sid in item_map and sid not in seen_ids:
                        seen_ids.add(sid)
                        item = item_map[sid]
                        # Explicitly carry over article_date
                        final_item = {
                            "id": item["id"],
                            "title": item["title"],
                            "title_zh": title_zh if title_zh else item["title"], # Use generated Chinese title or fallback to original
                            "link": item["link"],
                            "description": item["description"],
                            "content": item["content"],
                            "platform": item["platform"],
                            "article_date": item.get("article_date", ""),
                            "reason": reason
                        }
                        final_selection.append(final_item)
                
                # Create output directory
                timestamp = datetime.now().strftime('%Y-%m-%d-%H%M%S')
                output_dir = os.path.join('topics', timestamp)
                os.makedirs(output_dir, exist_ok=True)
                
                output_file = os.path.join(output_dir, 'aihot_selected.json')
                with open(output_file, 'w', encoding='utf-8') as f:
                    json.dump(final_selection, f, ensure_ascii=False, indent=2)
                print(f"Saved selected items to {output_file}")
                
                # Generate HTML Report
                generate_html_report(final_selection, output_dir)
                
            else:
                print("AI selection failed or returned no items.")
        else:
            print("No valid items to select from.")

    except Exception as e:
        print(f"An error occurred: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    scrape_aihot()
