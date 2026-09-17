---
name: ai-hot-content-curator
description: 一个用于发现、筛选和研究 AI/科技与财经/商业领域自媒体热门选题的技能。本 SKILL 只用于选题，写自媒体文案、写 C 哥日课等任何内容实际创作都不能使用这个 SKILL。
---

# ai-hot-content-curator

此技能旨在帮助发现、筛选和深度分析热门的自媒体内容的潜在选题。它专注于从网上获取热门最新新闻，然后聚焦于 AI、科技、财经/商业和“一人公司”等主题。

## 选题方向 (Focus)

`resources/content_curator_sources.json` 的 `focus` 字段决定筛选逻辑，当前为 `finance`（财经/商业）。

- `finance`：财报、商业模式、金融科技、资本市场、SaaS 运营、AI 的商业影响。
- `ai`：AI 产品与工作流案例、AI 对普通人的影响。

临时切换：设置环境变量 `CONTENT_FOCUS=ai`（或 `finance`），优先级高于配置文件。

财经方向的固定来源，已写入配置的「财经/商业深度」分组：

| 来源 | RSS |
| --- | --- |
| The Diff | `https://www.thediff.co/archive/rss` |
| Stratechery | `https://stratechery.com/feed/` |
| MIT Sloan Management Review | `https://sloanreview.mit.edu/feed/` |
| SaaStr | `https://www.saastr.com/feed/` |
| TechCrunch Fintech | `https://techcrunch.com/category/fintech/feed/` |
| Fintech Blueprint | `https://lex.substack.com/feed` |

## 工作流

### 1. 自动获取与筛选 (Automated Curation)
- **执行脚本**: 运行 `scripts/scrape_aihot.py`。
- **功能**: 
    - 该脚本会自动从预设的来源（AIHot 网站、RSS 源等）抓取最新内容。
    - 自动进行去重、清洗和初步筛选（去除旧内容、无干货内容）。
    - 调用 AI 接口，从候选列表中挑选出最值得推荐的选题。
    - **中文输出**: 脚本会强制要求 AI 用中文生成推荐理由和优化后的中文标题。

### 2. 结果输出 (Output)
- **位置**: 脚本运行完成后，会在项目根目录下的 `topics/` 文件夹中创建一个带有时间戳的子目录（例如 `topics/2026-02-09-120000/`）。
- **文件**:
    - `aihot_selected.json`: 包含所有被选中选题的详细信息（JSON格式），包括 ID、中文标题、原始链接、推荐理由等。
    - `index.html`: 一个可视化的 HTML 报告，可以直接在浏览器中打开查看选题。

### 3. 后续操作 (Next Steps)
- 用户可以根据生成的 `index.html` 或 `aihot_selected.json` 确认最终选题。
- 确认选题后，如果需要进一步创作，请使用其他相关技能或手动进行深度研究。

## 使用方法
当用户需要自媒体选题，且通过热门新闻、最新爆点、热门事件等获取选题时，直接调用此技能运行 `scrape_aihot.py` 脚本即可。

## 注意事项
1. **依赖**: 脚本依赖 `requests`, `bs4`, `trafilatura`, `feedparser` 等库，请确保环境中已安装。
2. **输出**: 所有输出内容（标题、理由）均为中文。
3. **定位**: 脚本会自动在项目根目录生成 `topics` 文件夹，请在执行完脚本后检查该目录获取结果。
4. 本 SKILL 只用于选题，写自媒体文案、写 C 哥日课等任何内容实际创作都不能使用这个 SKILL。
