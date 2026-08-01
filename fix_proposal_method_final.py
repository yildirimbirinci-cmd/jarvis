from __future__ import annotations

from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(r"C:\Users\yildi\Desktop\artmach_assistant")
TARGET = PROJECT_ROOT / "core" / "assistant.py"

METHOD = r'''    def _generate_validated_own_code_proposal(
        self,
        prompt: str,
        *,
        max_attempts: int = 3,
    ) -> EditProposal:
        """Generate a syntactically valid, bounded proposal with feedback retries.

        Every failed attempt is fed back with its exact validator report. The
        same response is never accepted twice and existing files must be edited
        by exact anchors, so a small local model cannot corrupt a whole module by
        truncating or re-indenting it.
        """

        def is_anchor_error(value: object) -> bool:
            error_text = str(value or "")
            return "Patch anchor" in error_text and "bulunan=" in error_text

        attempts = max(1, min(int(max_attempts), 3))
        previous_response = ""
        previous_error = ""
        seen_responses: set[str] = set()
        failures: list[str] = []

        for attempt in range(1, attempts + 1):
            current_prompt = prompt

            if previous_error:
                current_prompt += (
                    "\n\nÖNCEKİ TASLAK REDDEDİLDİ. DOĞRULAYICI RAPORU:\n"
                    + previous_error[-6_000:]
                    + "\nAynı cevabı tekrarlama. Yalnızca bu rapordaki hatayı, "
                    "aynı dosya ve sembol kapsamında küçük operations kullanarak düzelt."
                )

            if is_anchor_error(previous_error):
                anchor_hints = self._unique_anchor_hints(prompt)
                if anchor_hints:
                    current_prompt += "\n\n" + anchor_hints

                current_prompt += (
                    "\n\nHATALI ESKİ JSON'U KOPYALAMA. "
                    "old veya anchor değerini yalnızca çalışan kaynak "
                    "bağlamından birebir seç. Kaynakta bulunmayan veya "
                    "birden fazla bulunan metni kullanma. Doğrulayıcı "
                    "raporunda GERÇEK KAYNAK BLOĞU verilmişse onu karakteri "
                    "karakterine aynen kullan."
                )

            if previous_response:
                current_prompt += (
                    "\n\nÖNCEKİ REDDEDİLEN JSON:\n"
                    + previous_response[-12_000:]
                )

            raw = self._request_code_model_json(
                current_prompt,
                temperature=0.0 if attempt > 1 else 0.05,
            )
            response_key = hashlib.sha256(
                raw.encode("utf-8", errors="replace")
            ).hexdigest()

            if response_key in seen_responses:
                duplicate_error = (
                    "Kod modeli önceki reddedilen taslağın aynısını tekrar üretti."
                )
                failures.append(f"deneme {attempt}: {duplicate_error}")

                if is_anchor_error(previous_error):
                    previous_response = ""
                else:
                    previous_error = duplicate_error
                    previous_response = raw
                continue

            seen_responses.add(response_key)
            previous_response = raw

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

                if is_anchor_error(previous_error):
                    previous_response = ""
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
                        previous_error += "\n\n" + guidance

                failures.append(f"deneme {attempt}: {previous_error}")
                try:
                    self.own_code_history.record(
                        "kod modeli taslağı doğrulamada reddedildi",
                        deneme=attempt,
                        hata=previous_error[:700],
                    )
                except Exception:
                    pass
                continue

            try:
                self.own_code_history.record(
                    "kod modeli doğrulanmış taslak üretti",
                    deneme=attempt,
                    dosya_sayısı=len(proposal.files),
                )
            except Exception:
                pass
            return proposal

        detail = " | ".join(failures[-3:]) or "geçerli taslak üretilemedi"
        raise WorkspaceError(
            f"Kod modeli {attempts} kontrollü denemede güvenli taslak üretemedi. "
            f"{detail}"
        )

'''


def main() -> None:
    if not TARGET.is_file():
        raise SystemExit(f"DOSYA BULUNAMADI: {TARGET}")

    text = TARGET.read_text(encoding="utf-8-sig")
    start = text.find("    def _generate_validated_own_code_proposal(")
    end = text.find("    def prepare_own_code_proposal(", start)

    if start < 0 or end < 0 or end <= start:
        raise SystemExit(
            "PROPOSAL METODU SINIRLARI BULUNAMADI. DOSYA DEĞİŞTİRİLMEDİ."
        )

    updated = text[:start] + METHOD + text[end:]
    compile(updated, str(TARGET), "exec")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = TARGET.with_name(f"assistant.py.backup_final_{stamp}")
    backup.write_text(text, encoding="utf-8", newline="\n")
    TARGET.write_text(updated, encoding="utf-8", newline="\n")

    print("SELF-DEVELOP PROPOSAL METODU TAM OLARAK DUZELTILDI")
    print(f"YEDEK: {backup}")


if __name__ == "__main__":
    main()
