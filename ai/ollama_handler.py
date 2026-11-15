"""
ai/ollama_handler.py
Unified AI engine: Supports Google AI Studio (Gemini) + Ollama (local) with smart fallback.
Generates anti-spam, human-like job application emails and high-converting subject lines.
"""

import requests
import random
import re
import sys
import os
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import Config


# ─────────────────────────────────────────────────────────────────────────────
# SPAM TRIGGER WORDS — LLM is instructed to NEVER use these
# ─────────────────────────────────────────────────────────────────────────────
SPAM_WORDS = [
    "guaranteed", "free", "urgent", "act now",
    "click here", "apply now", "don't miss", "limited time",
    "exclusive", "amazing", "incredible", "best ever",
    "100%", "no experience", "earn money", "work from home",
    "congratulations", "winner", "selected", "offer expires",
]

# ─────────────────────────────────────────────────────────────────────────────
# EMAIL BODY PROMPT — High-Impact Professional Style
# ─────────────────────────────────────────────────────────────────────────────
BODY_PROMPT = """You are {name}, a professional {role} with {experience} of hands-on experience.
Your technical core competencies: {skills}

A recruiter or hiring manager posted this opportunity on LinkedIn:
\"\"\"
{post_text}
\"\"\"

Write a polished, impressive, and professional job application email directly to this recruiter.

Follow this proven, attractive structure:
1. GREETING:
   - "Hi [Recruiter Name]," if their name is found in the post or headline.
   - Otherwise, "Hi there,"

2. OPENING & HOOK:
   - State clearly: "I came across your post regarding the [Specific Role Title / Tech Opportunity] and would like to express my strong interest."

3. VALUE PROPOSITION & TECH STACK:
   - Write 1-2 impactful paragraphs highlighting your practical expertise as a {role} with {experience} of experience.
   - Highlight your core technical skills: {skills}.
   - Emphasize building and shipping reliable production software, modern user interfaces, performant APIs/services, and scalable systems.
   - Mention key technologies and practical capabilities from your background that directly align with the requirements mentioned in the post.

4. ALIGNMENT & AVAILABILITY:
   - "I believe my hands-on background aligns well with the requirements of this role. I am open to discussing relevant opportunities and how I can contribute to your team."

5. CALL TO ACTION:
   - "Please find my updated resume attached for your review. I would welcome the opportunity to discuss this further."

CRITICAL GUIDELINES:
- Tone: Confident, skilled, articulate, and respectful.
- DO NOT use gimmicky spam buzzwords like: {spam_words}
- DO NOT write sign-offs like "Best regards" or contact details at the end (the system appends your verified signature automatically).
- Output ONLY the clean email body text without headers, subject, or quotes.

Write the email now:"""


# Technical and business abbreviations that should NOT be flagged as spam caps
TECH_ACRONYMS = {
    "AI", "ML", "DL", "CV", "LLM", "LLMS", "NLP", "RAG", "SQL", "AWS", "GCP",
    "API", "APIS", "GPU", "GPUS", "CUDA", "BERT", "GPT", "REST", "ETL", "MLOPS",
    "ANN", "CNN", "RNN", "SOTA", "PWC", "IBM", "TCS", "SAP", "AMD", "IIT", "NIT",
    "USA", "NYC", "HR", "CEO", "CTO", "VP", "IT", "MERN", "TRPC", "ORM", "JWT",
    "HTML", "CSS", "JS", "TS", "UI", "UX", "ERP", "CRM", "SEO"
}

# ─────────────────────────────────────────────────────────────────────────────
# SUBJECT LINE PROMPT — Modern, High-Converting Executive Style
# ─────────────────────────────────────────────────────────────────────────────
SUBJECT_PROMPT = """You are an expert technical recruiter and executive talent scout.
Write ONE modern, highly attractive email subject line for an application to this job post.

Job Post Details:
\"\"\"
{post_text_short}
\"\"\"

Applicant Profile:
- Full Name: {name}
- Current Role: {role}
- Experience: {experience}
- Core Skills: {skills}
- Target Company: {company}

DESIGN INSTRUCTIONS:
- The subject line must look polished, confident, and professional (like top tech engineers from Stripe, Meta, Google).
- Choose the best matching style below:
  * Style 1: Application: [Role] — [Your Name] ([Tech1] & [Tech2])
  * Style 2: [Role] Role at [Company] | [Your Name]
  * Style 3: [Your Name] — [Role] ([Tech1], [Tech2] | [Experience])
  * Style 4: Regarding the [Role] post — [Your Name]
- Pick the most relevant tech keywords from the post that match the applicant's skills.
- If company name is not specifically known, use Style 1 or Style 3.
- Length: 6 to 11 words. (Under 80 characters).
- FORBIDDEN: NO spam buzzwords ("Urgent", "Excited", "Hire me", "Greetings"), NO exclamation marks, NO all-caps words except technical acronyms.
- Output ONLY the final subject line text. NO quotation marks. NO explanation.

Subject Line:"""


# ─────────────────────────────────────────────────────────────────────────────
# SPAM CHECKER & TEXT CLEANERS
# ─────────────────────────────────────────────────────────────────────────────
def check_spam_score(text: str) -> dict:
    """
    Spam score checker.
    Returns score (0=clean, higher=spammier) and triggered words.
    Only flags actual shouting words (4+ letters all-caps like URGENT, FREE)
    while whitelisting tech and business acronyms.
    """
    text_lower = text.lower()
    triggered  = [w for w in SPAM_WORDS if w in text_lower]

    raw_caps = re.findall(r'\b[A-Z]{4,}\b', text)
    caps_words = [w for w in raw_caps if w.upper() not in TECH_ACRONYMS]

    exclamations = text.count("!")
    links = len(re.findall(r'http[s]?://', text))

    score = (
        len(triggered) * 3 +
        len(caps_words) * 2 +
        exclamations * 2 +
        links * 5
    )
    return {
        "score": score,
        "triggered_spam_words": triggered,
        "caps_words": caps_words,
        "exclamations": exclamations,
        "links": links,
        "is_clean": score == 0,
    }


def clean_email_body(body: str) -> str:
    """Post-process AI output to remove any remaining spam signals."""
    body = body.replace("!", ".")
    body = re.sub(r'http[s]?://\S+', '', body)

    def clean_caps(m):
        w = m.group(1)
        return w if w.upper() in TECH_ACRONYMS else w.title()

    body = re.sub(r'\b([A-Z]{3,})\b', clean_caps, body)
    body = re.sub(r'\n{3,}', '\n\n', body).strip()
    return body


def clean_subject(subject: str) -> str:
    """Remove spam signals and prefixes from subject line."""
    subject = subject.strip().strip('"\'`')
    subject = re.sub(r'^(?:subject|re)\s*[:\-]\s*', '', subject, flags=re.IGNORECASE)
    subject = subject.replace("!", "")

    def clean_caps(m):
        w = m.group(1)
        return w if w.upper() in TECH_ACRONYMS else w.title()

    subject = re.sub(r'\b([A-Z]{3,})\b', clean_caps, subject)
    return subject.strip()


# ─────────────────────────────────────────────────────────────────────────────
# CIRCUIT BREAKER STATE FLAGS
# ─────────────────────────────────────────────────────────────────────────────
_gemini_quota_exhausted = False
_gemini_consecutive_429 = 0
_ollama_unavailable = False


# ─────────────────────────────────────────────────────────────────────────────
# GOOGLE AI STUDIO (GEMINI) CALLER
# ─────────────────────────────────────────────────────────────────────────────
def _call_gemini(prompt: str, max_tokens: int = 1000, temperature: float = 0.7) -> str | None:
    """
    Call Google AI Studio (Gemini) REST API.
    Tries primary configured model, with automatic failover to backup model.
    Handles temporary 503 load spikes gracefully and trips circuit breaker on persistent 429.
    """
    global _gemini_quota_exhausted, _gemini_consecutive_429
    if _gemini_quota_exhausted or not Config.GEMINI_API_KEY:
        return None

    candidate_models = ["gemini-flash-lite-latest", "gemini-3.1-flash-lite-preview"]
    if Config.GEMINI_MODEL not in candidate_models:
        candidate_models.insert(0, Config.GEMINI_MODEL)

    quota_exhausted_count = 0
    had_429 = False

    for model_name in candidate_models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={Config.GEMINI_API_KEY}"
        gen_config = {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        }
        if "gemini-3" in model_name or "gemini-2.5" in model_name:
            if "lite" not in model_name:
                gen_config["thinkingConfig"] = {"thinkingBudget": 0}

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": gen_config
        }

        # Up to 2 attempts for transient errors
        for attempt in range(1, 3):
            try:
                resp = requests.post(url, json=payload, timeout=20)
                if resp.status_code == 200:
                    _gemini_consecutive_429 = 0
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts and "text" in parts[0]:
                            return parts[0]["text"].strip()
                elif resp.status_code == 503:
                    # Google server temporary capacity spike (NOT a quota error)
                    if attempt == 1:
                        print(f"[Gemini] Server temporarily busy (503) on '{model_name}'. Retrying in 1.5s...")
                        time.sleep(1.5)
                        continue
                    else:
                        print(f"[Gemini] '{model_name}' busy (503) — trying alternate model...")
                        break
                elif resp.status_code == 429:
                    had_429 = True
                    err_text = resp.text.lower()
                    if "quota" in err_text or "resource_exhausted" in err_text or "limit exceeded" in err_text:
                        print(f"[Gemini] ⚠️ Model '{model_name}' daily quota exhausted.")
                        quota_exhausted_count += 1
                        break
                    else:
                        if attempt == 1:
                            print(f"[Gemini] Rate limit hit on '{model_name}'. Retrying in 2s...")
                            time.sleep(2)
                            continue
                        else:
                            break
                else:
                    print(f"[Gemini] API returned HTTP {resp.status_code} for {model_name}")
                    break
            except Exception as e:
                print(f"[Gemini] Request failed for {model_name}: {e}")
                break

    if had_429:
        _gemini_consecutive_429 += 1

    # Trip permanent quota breaker if candidate models exhausted or persistent 429
    if (quota_exhausted_count >= len(candidate_models) or _gemini_consecutive_429 >= 2) and not _gemini_quota_exhausted:
        _gemini_quota_exhausted = True
        print("\n[AI] ⚡ Gemini quota / rate limit reached. Instantly switching to curated templates for all remaining emails.")

    return None


# ─────────────────────────────────────────────────────────────────────────────
# OLLAMA CALLER
# ─────────────────────────────────────────────────────────────────────────────
def _call_ollama(prompt: str, max_tokens: int = 600, temperature: float = 0.7) -> str | None:
    """Call Ollama API. Drops instantly if Ollama connection fails."""
    global _ollama_unavailable
    if _ollama_unavailable:
        return None

    try:
        response = requests.post(
            f"{Config.OLLAMA_BASE_URL}/api/generate",
            json={
                "model": Config.OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens,
                }
            },
            timeout=5
        )
        if response.status_code == 200:
            data = response.json()
            resp_text = (data.get("response") or "").strip()
            if resp_text:
                return resp_text
    except Exception:
        _ollama_unavailable = True

    return None


# ─────────────────────────────────────────────────────────────────────────────
# UNIFIED AI CALLER
# ─────────────────────────────────────────────────────────────────────────────
def _call_ai(prompt: str, max_tokens: int = 700, temperature: float = 0.7) -> tuple[str | None, str]:
    """
    Unified AI caller:
    1. Tries Google AI Studio (Gemini) if GEMINI_API_KEY is configured and quota not tripped.
    2. Falls back to Ollama if available.
    3. Falls back immediately to Curated Template without delay.
    Returns: (text, provider_label)
    """
    if Config.GEMINI_API_KEY and not _gemini_quota_exhausted:
        gemini_text = _call_gemini(prompt, max_tokens=max_tokens, temperature=temperature)
        if gemini_text:
            return gemini_text, f"Google Gemini ({Config.GEMINI_MODEL})"

    if not _ollama_unavailable:
        ollama_text = _call_ollama(prompt, max_tokens=max_tokens, temperature=temperature)
        if ollama_text:
            return ollama_text, f"Ollama ({Config.OLLAMA_MODEL})"

    return None, "Curated Template"


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────
def generate_email_body(post_text: str, company: str = "") -> str:
    """
    Generate a spam-safe, human-like email body using active AI engine (Gemini / Ollama).
    Falls back to clean template if AI unavailable.
    """
    prompt = BODY_PROMPT.format(
        name=Config.YOUR_NAME,
        role=Config.YOUR_ROLE,
        experience=Config.YOUR_EXPERIENCE,
        skills=Config.YOUR_SKILLS,
        post_text=post_text[:1200],
        spam_words=", ".join(SPAM_WORDS[:12]),
    )

    body, provider = _call_ai(prompt, max_tokens=700, temperature=0.7)

    if not body:
        print(f"[AI] ⚠️  No response from AI models — using fallback template")
        return _fallback_email_body(post_text)

    body = clean_email_body(body)

    spam = check_spam_score(body)
    if spam["score"] > 0:
        print(f"[AI] Spam score: {spam['score']} | Issues: {spam}")
    else:
        print(f"[AI] Email is spam-clean (score: 0)")

    print(f"[AI] Email generated ({len(body.split())} words) via {provider}")
    return body


def generate_subject_line(post_text: str, company: str = "") -> str:
    """
    Generate a modern, attractive, high-converting subject line using active AI engine.
    Falls back to polished executive template if AI unavailable.
    """
    prompt = SUBJECT_PROMPT.format(
        post_text_short=post_text[:500],
        name=Config.YOUR_NAME,
        role=Config.YOUR_ROLE,
        experience=Config.YOUR_EXPERIENCE,
        skills=Config.YOUR_SKILLS,
        company=company or "your team",
    )

    subject, provider = _call_ai(prompt, max_tokens=60, temperature=0.6)

    if not subject:
        return _fallback_subject(company)

    subject = clean_subject(subject)

    if not subject or len(subject) > 85 or len(subject) < 8:
        print(f"[AI] Subject length {len(subject)} out of range — using fallback subject")
        return _fallback_subject(company)

    spam = check_spam_score(subject)
    if spam["score"] > 0:
        print(f"[AI] Subject spam score {spam['score']} — using fallback subject")
        return _fallback_subject(company)

    print(f"[AI] Subject: '{subject}' (via {provider})")
    return subject


# ─────────────────────────────────────────────────────────────────────────────
# FALLBACK TEMPLATES (spam-clean, human-written style)
# ─────────────────────────────────────────────────────────────────────────────
_FALLBACK_BODIES = [
    """Hi there,

I came across your post and noticed you're looking for someone with a strong background in {role}. I have {experience} of experience building full-stack applications with {skills}.

I would love to explore if there is a mutual fit. Please find my updated resume attached for your review — looking forward to connecting.

{name}
{role}""",

    """Hello,

Your post caught my attention — the work you described aligns closely with my hands-on background over the past {experience}.

My core stack includes {skills}, and I focus on delivering clean, scalable, and user-centric software. I've attached my resume and would welcome a brief conversation.

{name}
{role}""",

    """Hi,

I saw your hiring post and wanted to reach out directly. I'm a {role} with {experience} of experience, working primarily with {skills}.

I believe I could bring immediate value to your engineering team. My resume is attached — would love to discuss further.

{name}
{role}""",
]

_FALLBACK_SUBJECTS = [
    "Application: {role} — {name} ({experience} Exp)",
    "Application: {role} at {company} — {name}",
    "{name} | {role} ({skill1} & {skill2})",
    "{role} Opportunity — {name} (Resume Attached)",
    "Application: {role} — {name} ({skill1}, {skill2})",
]


def _fallback_email_body(post_text: str = "") -> str:
    """Pick a random spam-clean fallback template"""
    template = random.choice(_FALLBACK_BODIES)
    return template.format(
        name=Config.YOUR_NAME,
        role=Config.YOUR_ROLE,
        experience=Config.YOUR_EXPERIENCE,
        skills=Config.YOUR_SKILLS.strip(),
    )


def _fallback_subject(company: str = "") -> str:
    """Pick a random spam-clean fallback subject"""
    template = random.choice(_FALLBACK_SUBJECTS)
    skills_list = [s.strip() for s in Config.YOUR_SKILLS.split(",")]
    return template.format(
        name=Config.YOUR_NAME,
        role=Config.YOUR_ROLE,
        experience=Config.YOUR_EXPERIENCE,
        company=company or "your team",
        skill1=skills_list[0] if len(skills_list) > 0 else "TypeScript",
        skill2=skills_list[1] if len(skills_list) > 1 else "React",
    )


def check_ollama_status() -> dict:
    """
    Check active AI provider status:
    Prioritizes Gemini if GEMINI_API_KEY is set, otherwise checks Ollama.
    Maintains backward compatibility with main.py.
    """
    if Config.GEMINI_API_KEY:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{Config.GEMINI_MODEL}?key={Config.GEMINI_API_KEY}"
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                return {
                    "running": True,
                    "provider": "gemini",
                    "configured_model": Config.GEMINI_MODEL,
                    "model_available": True,
                    "available_models": [Config.GEMINI_MODEL],
                    "info": f"Google AI Studio ({Config.GEMINI_MODEL})",
                }
            else:
                print(f"[AI] Gemini model check ({Config.GEMINI_MODEL}) returned HTTP {resp.status_code}")
        except Exception as e:
            print(f"[AI] Gemini check failed: {e}")

    try:
        resp = requests.get(f"{Config.OLLAMA_BASE_URL}/api/tags", timeout=5)
        models = [m["name"] for m in resp.json().get("models", [])]
        return {
            "running": True,
            "provider": "ollama",
            "available_models": models,
            "configured_model": Config.OLLAMA_MODEL,
            "model_available": any(Config.OLLAMA_MODEL in m for m in models),
            "info": f"Ollama ({Config.OLLAMA_MODEL})",
        }
    except Exception:
        return {
            "running": False,
            "provider": "none",
            "available_models": [],
            "configured_model": Config.OLLAMA_MODEL,
            "model_available": False,
            "info": "None (Using fallback templates)",
        }
