"""
wellfound/scraper/auto_apply.py
Automates the Wellfound native "Apply" button + modal submission.

Key fix: React modal overlay intercepts pointer events, so we use
JavaScript element.click() / dispatchEvent instead of Playwright .click()
to bypass overlay blocking.
"""

import time
import random
import requests
from playwright.sync_api import Page


def _js_click(page: Page, selector: str) -> bool:
    """Click an element via JavaScript — bypasses React modal overlay."""
    return page.evaluate(f"""
        () => {{
            const el = document.querySelector("{selector}");
            if (!el) return false;
            el.dispatchEvent(new MouseEvent('click', {{bubbles: true, cancelable: true}}));
            return true;
        }}
    """)


def _js_click_el(page, element) -> None:
    """Click a Playwright element handle via JavaScript — bypasses overlay."""
    element.evaluate("""el => {
        try { el.click(); } catch (e) {}
        el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
    }""")


def _generate_intro_note(job: dict, your_name: str, your_role: str,
                          your_skills: str, your_exp: str,
                          ollama_base: str, ollama_model: str,
                          max_words: int = 80) -> str:
    """Generate a short AI intro note for the Wellfound apply modal."""
    try:
        prompt = (
            f"You are {your_name}, a {your_role} with {your_exp} of experience.\n"
            f"Skills: {your_skills}\n\n"
            f"Write a SHORT (under {max_words} words), confident, professional intro note "
            f"for a job application to '{job.get('title', 'this role')}' at "
            f"'{job.get('company', 'this company')}'.\n"
            f"Job context:\n{job.get('description', '')[:500]}\n\n"
            "Note: 2-3 sentences only. No greetings, no sign-off. Just the pitch. "
            "Be direct and specific to their tech stack."
        )
        resp = requests.post(
            f"{ollama_base}/api/generate",
            json={"model": ollama_model, "prompt": prompt,
                  "stream": False, "options": {"num_predict": 150}},
            timeout=120,
        )
        text = (resp.json().get("response") or "").strip()
        if text:
            return text
    except Exception:
        pass

    # Fallback
    return (
        f"I'm a {your_role} with {your_exp} of hands-on experience in "
        f"{your_skills[:80]}. My background aligns well with what your team is building. "
        f"Happy to discuss further."
    )


def _close_any_blocking_modal(page: Page):
    """Dismiss any overlay/modal that might block the apply modal."""
    try:
        # Try pressing Escape to close any extra dialog
        close_btns = page.query_selector_all(
            "button[aria-label='Close'], "
            "button[aria-label='Dismiss'], "
            "[class*='close-button'], "
            "[class*='modal-close']"
        )
        for btn in close_btns:
            try:
                _js_click_el(page, btn)
                time.sleep(0.5)
            except Exception:
                pass
    except Exception:
        pass


def auto_apply_to_job(
    page: Page,
    job: dict,
    your_name: str,
    your_role: str,
    your_skills: str,
    your_exp: str,
    ollama_base: str,
    ollama_model: str,
    max_note_words: int = 80,
) -> dict:
    """
    Navigate to job page, click Apply, fill intro note, submit.
    Uses JS click to bypass React modal overlay blocking.
    """
    result = {
        "job_id":      job.get("job_id", ""),
        "title":       job.get("title", ""),
        "company":     job.get("company", ""),
        "apply_url":   job.get("apply_url", ""),
        "status":      "failed",
        "message":     "",
        "is_external": False,
    }

    try:
        url = job.get("apply_url", "")
        if not url:
            result["message"] = "No apply URL"
            return result

        page.goto(url, wait_until="load", timeout=30000)
        time.sleep(random.uniform(2, 3))

        # ── Find Active Apply button first ────────────────────────
        # If an active Apply button exists, we have not applied yet!
        apply_btn = page.query_selector(
            "button:has-text('Apply'):not([disabled]), "
            "button:has-text('Easy Apply'):not([disabled]), "
            "[data-test='apply-button']:not([disabled]), "
            "a:has-text('Apply'):not([disabled])"
        )

        if not apply_btn:
            # If no active Apply button, check if it's already applied or expired
            already = page.query_selector(
                "main button:has-text('Applied'), "
                "button[disabled]:has-text('Applied'), "
                "div:has-text('You applied on'), "
                "div:has-text('Application submitted')"
            )
            if already:
                result["status"]  = "already_applied"
                result["message"] = "Already applied to this job"
                return result

            result["message"] = "Apply button not found on page"
            return result

        # Check if external ATS link
        tag  = apply_btn.evaluate("el => el.tagName.toLowerCase()")
        href = apply_btn.get_attribute("href") or ""
        if tag == "a" and href and "wellfound.com" not in href:
            result["status"]      = "external_ats"
            result["is_external"] = True
            result["message"]     = f"External ATS: {href}"
            return result

        # ── Click Apply via JS (bypasses overlay) ─────────────────
        apply_btn.scroll_into_view_if_needed()
        time.sleep(0.5)
        _js_click_el(page, apply_btn)
        time.sleep(random.uniform(2, 3))

        # ── Wait for modal to appear ──────────────────────────────
        try:
            page.wait_for_selector(
                ".ReactModalPortal, [class*='modal'], [role='dialog']",
                timeout=8000
            )
        except Exception:
            pass

        time.sleep(1)

        # ── Fill intro note if present ────────────────────────────
        note_field = page.query_selector(
            "textarea[name='userNote'], "
            "textarea[id='form-input--userNote'], "
            "textarea[placeholder*='note'], "
            "textarea[placeholder*='Write a note'], "
            "textarea[placeholder*='intro']"
        )

        if note_field:
            intro = _generate_intro_note(
                job, your_name, your_role, your_skills, your_exp,
                ollama_base, ollama_model, max_note_words
            )
            # Use native React property setter + input event so React state updates
            try:
                note_field.evaluate("""(el, val) => {
                    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')?.set;
                    if (setter) {
                        setter.call(el, val);
                    } else {
                        el.value = val;
                    }
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }""", intro)
            except Exception:
                try:
                    note_field.fill(intro)
                except Exception:
                    pass
            time.sleep(random.uniform(0.8, 1.5))
            print(f"[WF Apply] ✍️  Intro note filled ({len(intro.split())} words)")
        else:
            print(f"[WF Apply] ℹ️  No intro note field — submitting directly")

        # ── Handle any screening questions ────────────────────────
        # Click "Yes" radio/checkboxes for work auth etc.
        yes_labels = page.query_selector_all(
            "label:has-text('Yes'), "
            "input[value='yes'], "
            "input[value='true']"
        )
        for lbl in yes_labels:
            try:
                _js_click_el(page, lbl)
                time.sleep(0.2)
            except Exception:
                pass

        # ── Find and click Submit via JS ──────────────────────────
        submit_btn = page.query_selector(
            ".ReactModalPortal button[type='submit'], "
            ".ReactModalPortal button:has-text('Submit'), "
            ".ReactModalPortal button:has-text('Apply'), "
            ".ReactModalPortal button:has-text('Send'), "
            "[role='dialog'] button[type='submit'], "
            "[role='dialog'] button:has-text('Submit')"
        )

        if not submit_btn:
            # Broader fallback
            submit_btn = page.query_selector(
                "button[type='submit'], "
                "button:has-text('Submit application'), "
                "button:has-text('Send application')"
            )

        if submit_btn:
            try:
                submit_btn.scroll_into_view_if_needed()
            except Exception:
                pass
            time.sleep(0.5)
            _js_click_el(page, submit_btn)
            time.sleep(random.uniform(2, 3))

            # Confirm success
            success_el = page.query_selector(
                "[class*='success'], "
                "div:has-text('Application submitted'), "
                "div:has-text('Applied'), "
                "h2:has-text('Application sent')"
            )
            if success_el:
                result["status"]  = "applied"
                result["message"] = "Successfully applied via Wellfound"
            else:
                # Check if modal closed (good sign)
                modal_gone = page.query_selector(".ReactModalPortal") is None
                result["status"]  = "applied"
                result["message"] = "Submitted" + (" (modal closed)" if modal_gone else " (unconfirmed)")
        else:
            result["message"] = "Submit button not found in modal"

    except Exception as e:
        result["status"]  = "failed"
        result["message"] = str(e)[:200]

    return result


def json_safe(text: str) -> str:
    """Escape text for safe embedding in JS string literals."""
    return (
        repr(text)
        .replace("\\n", "\\\\n")
        .replace("\\r", "\\\\r")
        .replace("\\t", "\\\\t")
    )
