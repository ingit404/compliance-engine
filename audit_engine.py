import os
import sys
import json
import pandas as pd
import fitz
from typing import List,Dict
from contextlib import contextmanager
from google import genai
from google.genai import types

from prompts import BASE_AUDIT_PROMPT
from dotenv import load_dotenv
load_dotenv()
#prompt_builder(helper fucnk)

def build_final_prompt(user_instructions: str = "") -> str:
    """
    Safely appends optional user instructions to the base audit prompt.
    """
    if user_instructions:
        return (
            BASE_AUDIT_PROMPT
            + "\n\n### ADDITIONAL USER INSTRUCTIONS\n"
            + user_instructions.strip()
        )

    return BASE_AUDIT_PROMPT

#llm parser (helper funck)
def parse_model_output(response_text: str) -> List[Dict]:
    "clens llm output"
    clean = response_text.replace("```json", "").replace("```", "")
    clean=clean.strip().strip("'")
    return json.loads(clean)

#llm aduitor 

def run_llm_audit (
        ground_truth:str,
        clm:str,
        GL_regulation:str,
        target_doc:str,
        user_prompt: str,
        output_excel_path: str
) -> List[Dict]:
    """Runs compliance with 2 gemini models"""

    api_key=os.getenv('GEMINI_API_KEY')

    if not api_key:
        raise RuntimeError("API key is missing in the environment")
    client=genai.Client(api_key=api_key)

    # Upload PDFs to Gemini

    ground_truth_file = client.files.upload(file=ground_truth)
    clm_file = client.files.upload(file=clm)
    gl_file = client.files.upload(file=GL_regulation)
    target_file = client.files.upload(file=target_doc)

    model_1="gemini-3-pro-preview"
    model_2 = "gemini-3-flash-preview"
    user_prompt =user_prompt or "" 
    final_prompt=build_final_prompt(user_prompt)

    #model_1_response
    
    response_1=client.models.generate_content(
        model=model_1,
        contents=[ground_truth_file,
                 clm_file,
                 gl_file, 
                 target_file,
                 final_prompt],
        config=types.GenerateContentConfig(temperature=0.1)
    )
    data_1=parse_model_output(response_1.text)

    #model_2_response
    
    response_2=client.models.generate_content(
        model=model_2,
        contents=[ground_truth_file,
                 clm_file,
                 gl_file, 
                 target_file,
                 final_prompt],
        config=types.GenerateContentConfig(temperature=0.1)
    )
    data_2=parse_model_output(response_2.text)

    #merger+deduplicated
    def make_key(item: Dict):
        return (
            item.get("page_number"),
            item.get("word/phrase_highlighted", "").strip().lower(),
            item.get("whats_wrong", "").strip().lower()
        )

    merged = {}

    for item in data_1:
        merged[make_key(item)] = {
            **item,
            "from_model": model_1
        }

    for item in data_2:
        key = make_key(item)
        if key in merged:
            merged[key]["from_model"] = "model_1, model_2"
        else:
            merged[key] = {
                **item,
                "from_model": model_2
            }

    final_data = list(merged.values())

    # ---------- Write Excel ----------
    final_df = pd.DataFrame(final_data)
    final_df.to_excel(output_excel_path, index=False)

    return final_data


#pdf_highlighting 

@contextmanager
def silence_mupdf():
    """
    Suppresses PyMuPDF stderr noise.
    """
    old_stderr = sys.stderr
    sys.stderr = open(os.devnull, "w")
    try:
        yield
    finally:
        sys.stderr.close()
        sys.stderr = old_stderr


def normalize_token(t: str) -> str:
    if not t:
        return ""
    return (
        t.replace("\n", " ")
         .replace("\r", " ")
         .replace("\u00ad", "")
         .replace("\xa0", " ")
         .replace("*", "")
         .replace(":", "")
         .replace("|", "")
         .replace("(", "")
         .replace(")", "")
         .replace("%", "")
         .replace(",", "")
         .lower()
         .strip()
    )


def find_phrase_rects_word_level(page, phrase: str):
    """
    Word-level fallback search for phrases not found by search_for.
    """
    words = page.get_text("words")
    if not words:
        return []

    phrase_tokens = [
        normalize_token(t)
        for t in phrase.split()
        if normalize_token(t)
    ]

    page_tokens = [normalize_token(w[4]) for w in words]
    rects = []
    window = len(phrase_tokens)

    for i in range(len(page_tokens)):
        chunk = page_tokens[i:i + window + 2]
        joined = " ".join(chunk)

        if all(p in joined for p in phrase_tokens):
            for j in range(i, min(i + window + 2, len(words))):
                rects.append(fitz.Rect(words[j][:4]))

    return rects


def highlight_pdf(
    pdf_path: str,
    output_path: str,
    data: List[Dict]
):
    """
    Highlights identified compliance issues in the PDF
    and writes an annotated output PDF.
    """

    with silence_mupdf():
        doc = fitz.open(pdf_path)

        for item in data:
            try:
                page_no = int(item["page_number"]) - 1
                phrase = item["word/phrase_highlighted"].strip()
                note = item.get("whats_wrong", "").strip()
            except Exception:
                continue

            if not phrase or page_no < 0 or page_no >= len(doc):
                continue

            page = doc[page_no]

            flags = 0
            if hasattr(fitz, "TEXT_IGNORECASE"):
                flags |= fitz.TEXT_IGNORECASE
            if hasattr(fitz, "TEXT_DEHYPHENATE"):
                flags |= fitz.TEXT_DEHYPHENATE

            rects = page.search_for(phrase, flags=flags)

            if not rects:
                rects = find_phrase_rects_word_level(page, phrase)

            for rect in rects:
                annot = page.add_highlight_annot(rect)
                if note:
                    annot.set_info(
                        title="⚠️ Compliance Observation",
                        content=note
                    )
                annot.update(opacity=0.4)

        doc.save(
            output_path,
            garbage=4,
            deflate=True,
            clean=True
        )
        doc.close()