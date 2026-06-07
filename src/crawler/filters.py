"""Keyword-based filter for LLM/AI relevance."""

# 關鍵詞：標題中包含這些才視為 LLM 相關
LLM_KEYWORDS = [
    # 模型名稱
    "LLM", "LLaMA", "Llama", "GPT", "Claude", "Gemini", "DeepSeek", "Mistral",
    "Qwen", "Falcon", "Mixtral", "Phi-", "Grok", "o1", "o3", "o4",
    "Stable Diffusion", "DALL-E", "Midjourney", "Sora", "FLUX",
    # 技術關鍵詞
    "transformer", "attention", "fine-tun", "RLHF", "DPO", "LoRA",
    "embedding", "RAG", "vector DB", "tokeniz", "KV cache",
    "inference", "quantiz", "GGUF", "AWQ", "GPTQ",
    "prompt", "chain-of-thought", "CoT", "agent",
    # 平台/工具
    "LangChain", "LlamaIndex", "vLLM", "Ollama", "HuggingFace",
    "OpenAI", "Anthropic", "Google AI", "Meta AI", "xAI",
    # 概念
    "AI model", "language model", "large language", "foundation model",
    "artificial intelligence", "machine learning",
    # 學術
    "NeurIPS", "ICML", "ICLR", "ACL", "EMNLP",
    # 硬體
    "GPU", "TPU", "H100", "H200", "B200", "A100", "RTX 5090",
]


def is_llm_relevant(title: str) -> bool:
    """Check if a title is LLM/AI relevant based on keywords."""
    # 先排除不相干的
    exclude = [
        "crypto", "bitcoin", "blockchain", "NFT",
        "startup", "funding", "raised $", "Series ",
    ]
    title_lower = title.lower()
    for kw in exclude:
        if kw.lower() in title_lower:
            # But don't exclude if it's also clearly AI-related
            if not any(k.lower() in title_lower for k in LLM_KEYWORDS):
                return False

    for kw in LLM_KEYWORDS:
        if kw.lower() in title_lower:
            return True
    return False


def filter_items(items: list, min_score: int = 5) -> list:
    """Filter items by LLM relevance and minimum score."""
    relevant = []
    for item in items:
        if is_llm_relevant(item.title) and item.score >= min_score:
            relevant.append(item)
    return relevant
