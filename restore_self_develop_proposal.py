from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

PROJECT = Path(r"C:\Users\yildi\Desktop\artmach_assistant")
PATH = PROJECT / "core" / "assistant.py"

if not PATH.is_file():
    raise SystemExit(f"Dosya bulunamadi: {PATH}")

text = PATH.read_text(encoding="utf-8-sig")
start = text.find("    def _generate_validated_own_code_proposal(")
end = text.find("    def prepare_own_code_proposal(", start)

if start < 0 or end < 0 or end <= start:
    raise SystemExit("Proposal metodu sinirlari bulunamadi. Dosya degistirilmedi.")

required_names = (
    "repair_unique_whitespace_anchors",
    "repair_ambiguous_replace_anchors",
    "build_ambiguous_anchor_guidance",
    "build_missing_anchor_guidance",
)
missing = [name for name in required_names if name not in text]
if missing:
    raise SystemExit(
        "Gerekli import veya fonksiyon adlari eksik: " + ", ".join(missing)
    )

replacement = '''    def _generate_validated_own_code_proposal(
        self,
        prompt: str,
        *,
        max_attempts: int = 3,
    ) -> EditProposal:
        """Generate a syntactically valid, bounded proposal with feedback retries."""

        def is_anchor_cardinality_error(value: object) -> bool:
            error_text = str(value or "")
            return "Patch anchor" in error_text and "bulunan=" in error_text

        attempts = max(1, min(int(max_attempts), 3))
        previous_response = ""
        previous_error = ""
        seen_responses: set[str] = set()
        failures: list[str] = []

        for attempt in range(1, attempts + 1):
            current_prompt = prompt
            anchor_error_active = is_anchor_cardinality_error(previous_error)

            if previous_error:
                current_prompt += (
                    "\\n\\nONCEKI TASLAK REDDEDILDI. DOGRULAYICI RAPORU:\\n"
                    + previous_error[-6_000:]
                    + "\\nAyni cevabi tekrarlama. Yalnizca rapordaki hatayi, "
                    "ayni dosya ve sembol kapsaminda kucuk operations "
                    "kullanarak duzelt."
                )

            if anchor_error_active:
                anchor_hints = self._unique_anchor_hints(prompt)
                if anchor_hints:
                    current_prompt += "\\n\\n" + anchor_hints

                current_prompt += (
                    "\\n\\nZORUNLU ANCHOR KURALI:\\n"
                    "- Onceki JSON icindeki hatali old veya anchor degerini "
                    "tekrar kullanma.\\n"
                    "- DOGRULAYICI RAPORUNDA GERCEK KAYNAK BLOGU verildiyse "
                    "onu karakter karakter, girintisi ve tirnaklariyla aynen "
                    "kullan.\\n"
                    "- Gercek kaynak blogu uygun degilse islemi daha kucuk ve "
                    "benzersiz bir exact anchor ile yeniden tasarla.\\n"
                    "- Kaynakta tam olarak bir kez bulunmayan anchor yazma."
                )

            if previous_response and not anchor_error_active:
                current_prompt += (
                    "\\n\\nONCEKI REDDEDILEN JSON:\\n"
                    + previous_response[-12_000:]
                )

            if attempt > 1:
                try:
                    retry_path = DATA_DIR / "own_code" / "last_retry_prompt.txt"
                    retry_path.parent.mkdir(parents=True, exist_ok=True)
                    retry_path.write_text(current_prompt, encoding="utf-8")
                except Exception:
                    pass

            raw = self._request_code_model_json(
                current_prompt,
                temperature=0.0 if attempt > 1 else 0.05,
            )
            response_key = hashlib.sha256(
                raw.encode("utf-8", errors="replace")
            ).hexdigest()

            if response_key in seen_responses:
                duplicate_error = (
                    "Kod modeli onceki reddedilen taslagin aynisini tekrar uretti."
                )
                failures.append(f"deneme {attempt}: {duplicate_error}")

                if is_anchor_cardinality_error(previous_error):
                    previous_response = ""
                else:
                    previous_error = duplicate_error
                    previous_response = raw
                continue

            seen_responses.add(response_key)
            previous_response = raw
            payload: dict[str, object] | None = None

            try:
                payload = self._validate_own_code_payload_shape(raw)
                payload = merge_duplicate_operation_rows(payload)
                payload = repair_unique_whitespace_anchors(
                    payload,
                    project_root=self.own_project_root(),
                    instruction=prompt,
                )
                payload = repair_ambiguous_replace_anchors(
                    payload,
                    project_root=self.own_project_root(),
                    instruction=prompt,
                )
                canonical = json.dumps(payload, ensure_ascii=False)
                proposal = self.editor.create_proposal(canonical)
            except WorkspaceError as exc:
                previous_error = str(exc)

                try:
                    atomic_write_json(
                        DATA_DIR / "own_code" / "last_rejected_proposal.json",
                        {
                            "attempt": attempt,
                            "error": previous_error,
                            "raw_model_response": raw,
                        },
                        max_bytes=256 * 1024,
                    )
                except Exception:
                    pass

                if is_anchor_cardinality_error(previous_error):
                    previous_response = ""
                    guidance = ""

                    if isinstance(payload, dict):
                        guidance = build_ambiguous_anchor_guidance(
                            payload,
                            project_root=self.own_project_root(),
                            instruction=prompt,
                        )

                        if not guidance:
                            guidance = build_missing_anchor_guidance(
                                payload,
                                project_root=self.own_project_root(),
                                instruction=prompt,
                            )

                    if guidance:
                        previous_error += "\\n\\n" + guidance

                failures.append(f"deneme {attempt}: {previous_error}")
                try:
                    self.own_code_history.record(
                        "kod modeli taslagi dogrulamada reddedildi",
                        deneme=attempt,
                        hata=previous_error[:700],
                    )
                except Exception:
                    pass
                continue

            try:
                self.own_code_history.record(
                    "kod modeli dogrulanmis taslak uretti",
                    deneme=attempt,
                    dosya_sayisi=len(proposal.files),
                )
            except Exception:
                pass
            return proposal

        detail = " | ".join(failures[-3:]) or "gecerli taslak uretilemedi"
        raise WorkspaceError(
            f"Kod modeli {attempts} kontrollu denemede guvenli taslak uretemedi. "
            f"{detail}"
        )

'''

updated = text[:start] + replacement + text[end:]
compile(updated, str(PATH), "exec")

stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
backup = PATH.with_name(f"assistant.py.backup_{stamp}")
shutil.copy2(PATH, backup)
PATH.write_text(updated, encoding="utf-8", newline="\n")

print("SELF-DEVELOP PROPOSAL METODU GERI YUKLENDI")
print(f"YEDEK: {backup}")
