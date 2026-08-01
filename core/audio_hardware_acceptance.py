from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class AudioHardwareVoice(Protocol):
    def microphones(self) -> list[object]: ...

    def output_devices(self) -> list[object]: ...

    def resolve_working_microphone(
        self,
        requested_index: int | None,
        requested_name: str = "",
        status_callback=None,
    ) -> tuple[int, str, int]: ...

    def probe_output_device(
        self,
        output_device: int | None = None,
    ) -> dict[str, object]: ...

    def audio_route_status(self) -> dict[str, object]: ...

    def tts_backend_status(
        self,
        backend: str = "auto",
        piper_executable: str = "",
        piper_model: str = "",
    ) -> dict[str, object]: ...


@dataclass(frozen=True, slots=True)
class AudioHardwareCheck:
    name: str
    ok: bool
    detail: str


@dataclass(slots=True)
class AudioHardwareAcceptanceReport:
    checks: list[AudioHardwareCheck] = field(default_factory=list)
    microphone: dict[str, object] = field(default_factory=dict)
    output: dict[str, object] = field(default_factory=dict)
    recovery_note: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(check.ok for check in self.checks)

    def add(self, name: str, ok: bool, detail: str) -> None:
        self.checks.append(AudioHardwareCheck(str(name), bool(ok), str(detail)))

    def render(self) -> str:
        rows = [
            "Ses donanımı uygulama içi kabul testi: "
            + ("BAŞARILI" if self.ok else "BAŞARISIZ")
        ]
        rows.extend(
            f"[{'OK' if check.ok else 'HATA'}] {check.name}: {check.detail}"
            for check in self.checks
        )
        if self.recovery_note:
            rows.append(f"Otomatik toparlanma: {self.recovery_note}")
        rows.append(
            "Kısa test tonu gerçek PortAudio çıkışına yazıldı. Sesin fiziksel olarak "
            "duyulduğunu kullanıcı doğrulamalıdır; yazılım hoparlörün mekanik durumunu "
            "ölçemez."
        )
        return "\n".join(rows)


def _device_label(device: object) -> str:
    name = str(getattr(device, "name", "bilinmiyor"))
    host = str(getattr(device, "host_api", ""))
    index = getattr(device, "index", "?")
    return f"{name} [{host or 'API bilinmiyor'}] (indeks {index})"


def run_audio_hardware_acceptance(
    voice: AudioHardwareVoice,
    *,
    microphone_index: int | None,
    microphone_name: str = "",
    output_index: int | None,
    tts_backend: str = "auto",
    piper_executable: str = "",
    piper_model: str = "",
) -> AudioHardwareAcceptanceReport:
    """Exercise real input/output routes without internet or model inference."""

    report = AudioHardwareAcceptanceReport()

    try:
        microphones = list(voice.microphones())
    except Exception as exc:
        microphones = []
        report.add("mikrofon envanteri", False, f"Mikrofonlar okunamadı: {exc}")
    else:
        report.add(
            "mikrofon envanteri",
            bool(microphones),
            (
                f"{len(microphones)} kullanılabilir fiziksel giriş bulundu."
                if microphones
                else "Kullanılabilir fiziksel mikrofon bulunamadı."
            ),
        )

    if microphones:
        try:
            resolved_index, resolved_name, resolved_rate = (
                voice.resolve_working_microphone(
                    microphone_index,
                    requested_name=microphone_name,
                )
            )
            report.microphone = {
                "index": resolved_index,
                "name": resolved_name,
                "sample_rate": resolved_rate,
            }
            selected = next(
                (
                    item
                    for item in microphones
                    if int(getattr(item, "index", -1)) == int(resolved_index)
                ),
                None,
            )
            detail = (
                f"{_device_label(selected)} | {resolved_rate} Hz gerçek akış okuması geçti."
                if selected is not None
                else f"{resolved_name} | {resolved_rate} Hz gerçek akış okuması geçti."
            )
            report.add("mikrofon akışı", True, detail)
        except Exception as exc:
            report.add("mikrofon akışı", False, str(exc))

    try:
        outputs = list(voice.output_devices())
    except Exception as exc:
        outputs = []
        report.add("ses çıkışı envanteri", False, f"Çıkışlar okunamadı: {exc}")
    else:
        report.add(
            "ses çıkışı envanteri",
            bool(outputs),
            (
                f"{len(outputs)} kullanılabilir fiziksel çıkış bulundu."
                if outputs
                else "Kullanılabilir ses çıkışı bulunamadı."
            ),
        )

    if outputs:
        try:
            output = dict(voice.probe_output_device(output_index))
            report.output = output
            report.add(
                "gerçek çıkış akışı",
                True,
                (
                    f"{output.get('name', 'bilinmiyor')} "
                    f"[{output.get('host_api', 'API bilinmiyor')}] | "
                    f"{output.get('sample_rate', 0)} Hz | "
                    f"{output.get('channels', 0)} kanal test tonu yazıldı."
                ),
            )
        except Exception as exc:
            report.add("gerçek çıkış akışı", False, str(exc))

    try:
        backend = dict(
            voice.tts_backend_status(
                tts_backend,
                piper_executable,
                piper_model,
            )
        )
        detail = (
            f"seçim={backend.get('backend', 'auto')}; "
            f"Piper={'hazır' if backend.get('piper_ready') else 'hazır değil'} "
            f"({backend.get('piper_detail', '')}); "
            f"Windows TTS={'hazır' if backend.get('windows_ready') else 'hazır değil'} "
            f"({backend.get('windows_detail', '')})"
        )
        report.add("TTS yedekleme yolu", bool(backend.get("ready")), detail)
    except Exception as exc:
        report.add("TTS yedekleme yolu", False, str(exc))

    try:
        routes = dict(voice.audio_route_status())
        input_route = routes.get("input")
        output_route = routes.get("output")
        route_ok = bool(input_route and output_route)
        report.recovery_note = str(routes.get("last_recovery", ""))
        detail = (
            "Çalıştığı kanıtlanan giriş ve çıkış rotaları ad/API/örnekleme oranıyla "
            "yerel olarak kaydedildi."
            if route_ok
            else "Giriş veya çıkış rotası henüz kanıtlanıp kalıcılaştırılamadı."
        )
        store_error = str(routes.get("store_error", ""))
        if store_error:
            detail += f" Tercih kaydı uyarısı: {store_error}"
        report.add("kalıcı aygıt rotası", route_ok, detail)
    except Exception as exc:
        report.add("kalıcı aygıt rotası", False, str(exc))

    return report
