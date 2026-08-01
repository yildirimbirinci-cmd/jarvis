"""Evidence-based architecture assessment for Jarvis and user projects.

The service is deliberately read-only.  It composes existing static review,
complexity and dependency services instead of creating a second indexer.  A
finding is emitted only when the local project supplies concrete evidence.
Internet research and code changes remain separate, explicitly approved steps.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from artmach_assistant.core.architecture_service import ArchitectureService, DependencyGraph
from artmach_assistant.core.code_review import CodeReviewAnalysis, CodeReviewIssue, CodeReviewService
from artmach_assistant.core.complexity_analyzer import ComplexityAnalyzer, ComplexityItem
from artmach_assistant.core.project_index import IGNORED_DIRS
from artmach_assistant.core.workspace import WorkspaceError, WorkspaceService

_MAX_SCAN_FILES = 12_000
_MAX_FINDINGS = 120
_MAX_EVIDENCE = 24
_MAX_TEXT_BYTES = 1_000_000
_SOURCE_SUFFIXES = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt",
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".cs", ".go", ".rs",
    ".qml", ".swift", ".rb", ".php",
}
_LANGUAGE_LABELS = {
    ".py": "Python", ".pyi": "Python", ".js": "JavaScript", ".jsx": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".java": "Java", ".kt": "Kotlin",
    ".c": "C", ".cc": "C++", ".cpp": "C++", ".cxx": "C++", ".h": "C/C++",
    ".hpp": "C++", ".cs": "C#", ".go": "Go", ".rs": "Rust", ".qml": "QML",
    ".swift": "Swift", ".rb": "Ruby", ".php": "PHP",
}
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@dataclass(frozen=True, slots=True)
class ImprovementEvidence:
    source: str
    path: str
    line: int
    detail: str
    metric: str = ""

    @property
    def location(self) -> str:
        if self.path and self.line > 0:
            return f"{self.path}:{self.line}"
        return self.path or self.source


@dataclass(frozen=True, slots=True)
class ProjectProfile:
    languages: tuple[tuple[str, int], ...]
    frameworks: tuple[str, ...]
    manifests: tuple[str, ...]
    source_files: int
    test_files: int

    @property
    def stack_text(self) -> str:
        languages = ", ".join(name for name, _count in self.languages[:5]) or "bilinmeyen teknoloji"
        frameworks = ", ".join(self.frameworks[:8])
        return f"{languages}; {frameworks}" if frameworks else languages


@dataclass(frozen=True, slots=True)
class ImprovementFinding:
    finding_id: str
    severity: str
    category: str
    title: str
    explanation: str
    confidence: float
    evidence: tuple[ImprovementEvidence, ...]
    affected_paths: tuple[str, ...]
    recommendation: str
    acceptance_criteria: tuple[str, ...]
    research_query: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ProjectImprovementAssessment:
    root: str
    generated_at: str
    profile: ProjectProfile
    findings: tuple[ImprovementFinding, ...]
    scanned_files: int
    limitations: tuple[str, ...] = ()

    @property
    def has_findings(self) -> bool:
        return bool(self.findings)

    def finding(self, finding_id: str) -> ImprovementFinding | None:
        key = str(finding_id).strip().upper()
        return next((item for item in self.findings if item.finding_id.upper() == key), None)

    def research_queries(self, *, limit: int = 4) -> tuple[str, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("Araştırma sorgusu limiti pozitif tam sayı olmalıdır.")
        queries: list[str] = []
        for finding in self.findings:
            query = finding.research_query.strip()
            if query and query.casefold() not in {item.casefold() for item in queries}:
                queries.append(query)
            if len(queries) >= min(limit, 12):
                break
        return tuple(queries)

    def model_context(self, *, limit: int = 8) -> str:
        rows = [
            f"PROJE KÖKÜ: {self.root}",
            f"TEKNOLOJİ: {self.profile.stack_text}",
            "YEREL BULGULAR:",
        ]
        for finding in self.findings[: max(1, min(limit, 20))]:
            evidence = "; ".join(
                f"{item.location}: {item.detail}" for item in finding.evidence[:4]
            )
            rows.append(
                f"- {finding.finding_id} [{finding.severity}] {finding.title}\n"
                f"  Açıklama: {finding.explanation}\n"
                f"  Kanıt: {evidence}\n"
                f"  Yerel öneri: {finding.recommendation}"
            )
        if self.limitations:
            rows.append("SINIRLAR: " + "; ".join(self.limitations))
        return "\n".join(rows)

    def report(self, *, limit: int = 12) -> str:
        if isinstance(limit, bool) or not isinstance(limit, int):
            limit = 12
        output_limit = max(1, min(limit, 30))
        lines = [
            "KANITA DAYALI MİMARİ İYİLEŞTİRME RAPORU",
            f"Proje: {self.root}",
            f"Teknoloji: {self.profile.stack_text}",
            f"Kaynak dosyası: {self.profile.source_files} | Test dosyası: {self.profile.test_files}",
            f"İncelenen dosya: {self.scanned_files} | Bulgu: {len(self.findings)}",
            "",
        ]
        if not self.findings:
            lines.append(
                "Mevcut statik ve bağımlılık kontrolleriyle doğrulanmış mimari sorun bulunamadı. "
                "Bu sonuç çalışma zamanı, performans veya kullanıcı deneyimi hatası olmadığı anlamına gelmez."
            )
        for finding in self.findings[:output_limit]:
            lines.append(
                f"[{finding.finding_id}] {finding.severity.upper()} — {finding.title}\n"
                f"Neden: {finding.explanation}\n"
                f"Kanıt: "
                + "; ".join(
                    f"{item.location}: {item.detail}"
                    + (f" ({item.metric})" if item.metric else "")
                    for item in finding.evidence[:4]
                )
                + f"\nÖneri: {finding.recommendation}\n"
                + "Başarı ölçütü: " + "; ".join(finding.acceptance_criteria[:3])
            )
        hidden = len(self.findings) - output_limit
        if hidden > 0:
            lines.append(f"... {hidden} ek bulgu raporda gösterilmedi.")
        if self.limitations:
            lines.append("\nAnaliz sınırları: " + "; ".join(self.limitations))
        lines.append(
            "\nHiçbir dosya değiştirilmedi. İnternet karşılaştırması ve kod uygulaması ayrı, açık onay gerektirir."
        )
        return "\n\n".join(lines)


class ProjectImprovementService:
    """Compose current project services into ranked, evidence-backed findings."""

    def __init__(self, workspace: WorkspaceService) -> None:
        self.workspace = workspace
        self.reviewer = CodeReviewService(workspace)
        self.architecture = ArchitectureService(workspace)
        self.complexity = ComplexityAnalyzer(workspace)

    def analyze(self) -> ProjectImprovementAssessment:
        root = Path(self.workspace.require_root()).resolve(strict=False)
        profile, scanned_files, profile_limit = self._profile(root)
        review = self.reviewer.analyze()
        try:
            complexity = self.complexity.analyze(
                include_low_risk=False,
                limit=500,
            )
            complexity_items = complexity.items
            parse_failures = complexity.parse_failures
        except (WorkspaceError, OSError, ValueError, MemoryError, RecursionError) as exc:
            complexity_items = ()
            parse_failures = (f"Karmaşıklık analizi tamamlanamadı: {exc}",)
        try:
            graph = self.architecture.dependency_graph()
        except (WorkspaceError, OSError, ValueError, MemoryError, RecursionError):
            graph = DependencyGraph()

        findings: list[ImprovementFinding] = []
        findings.extend(self._review_findings(review, profile))
        findings.extend(self._complexity_findings(complexity_items, profile))
        findings.extend(self._dependency_cycle_findings(graph, profile))
        findings.extend(self._dependency_hotspot_findings(graph, profile))
        findings.extend(self._large_file_findings(root, profile))
        findings.extend(self._test_gap_findings(profile))
        findings = self._deduplicate_and_rank(findings)[:_MAX_FINDINGS]

        limitations: list[str] = []
        if profile_limit or review.scan_limit_reached:
            limitations.append("dosya tarama sınırına ulaşıldı")
        if review.issue_limit_reached:
            limitations.append("statik bulgu sınırına ulaşıldı")
        if parse_failures:
            limitations.append(
                f"{len(parse_failures)} Python dosyası karmaşıklık analizine alınamadı"
            )
        if not graph.outgoing and profile.source_files:
            limitations.append("yerel bağımlılık grafiği boş veya çözümlenemedi")

        return ProjectImprovementAssessment(
            root=str(root),
            generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            profile=profile,
            findings=tuple(findings),
            scanned_files=max(scanned_files, review.scanned_files),
            limitations=tuple(dict.fromkeys(limitations)),
        )

    def _review_findings(
        self,
        analysis: CodeReviewAnalysis,
        profile: ProjectProfile,
    ) -> list[ImprovementFinding]:
        findings: list[ImprovementFinding] = []
        # The legacy review's DUPLICATE signal is based on name/argument count
        # alone.  That is useful as a search hint but not strong enough to call
        # a project architecture defect, so it is deliberately excluded here
        # until a body/behaviour similarity check supplies real evidence.
        direct_kinds = {"SYNTAX", "SECURITY", "QUALITY"}
        for issue in analysis.issues:
            if issue.kind not in direct_kinds:
                continue
            if issue.kind == "SECURITY" and self._is_test_path(issue.path):
                # Test harnesses often exercise eval/exec and credential
                # detection intentionally.  Do not present those fixtures as
                # production security defects without runtime evidence.
                continue
            title_map = {
                "SYNTAX": "Kaynak dosyada sözdizimi hatası",
                "SECURITY": "Güvenlik açısından riskli kod deseni",
                "QUALITY": "Hata gizleyebilen kalite sorunu",
            }
            recommendation_map = {
                "SYNTAX": "Sözdizimi hatasını en küçük değişiklikle düzelt ve ilgili dosyayı derle.",
                "SECURITY": "Riskli deseni güvenli, sınırlandırılmış ve test edilebilir bir API ile değiştir.",
                "QUALITY": "Hata yakalama ve raporlama sınırını açık exception türleriyle daralt.",
            }
            findings.append(self._finding(
                severity=issue.severity,
                category=f"static_{issue.kind.casefold()}",
                title=f"{title_map[issue.kind]}: {issue.path}",
                explanation=issue.message,
                confidence=0.98 if issue.kind in {"SYNTAX", "SECURITY"} else 0.86,
                evidence=(self._evidence_from_review(issue),),
                paths=(issue.path,),
                recommendation=recommendation_map[issue.kind],
                acceptance=(
                    "Değiştirilen dosya yeniden statik kontrolden geçmeli.",
                    "Mevcut testlerde yeni hata oluşmamalı.",
                    "Düzeltme, raporlanan dosya ve davranışla sınırlı kalmalı.",
                ),
                research_query=self._research_query(profile, issue.kind, issue.message),
            ))

        todo_by_path: Counter[str] = Counter(
            issue.path for issue in analysis.issues if issue.kind == "TODO"
        )
        for path, count in todo_by_path.most_common(8):
            if count < 3:
                continue
            examples = tuple(
                self._evidence_from_review(issue)
                for issue in analysis.issues
                if issue.kind == "TODO" and issue.path == path
            )[:6]
            findings.append(self._finding(
                severity="low",
                category="unfinished_work_cluster",
                title=f"Tamamlanmamış işaretler yoğunlaşmış: {path}",
                explanation=f"Aynı dosyada {count} TODO/FIXME/HACK işareti bulundu.",
                confidence=0.80,
                evidence=examples,
                paths=(path,),
                recommendation="İşaretleri davranış gereksinimi, teknik borç veya artık geçersiz not olarak sınıflandır; doğrulanmış olanları ayrı görev ve testlere dönüştür.",
                acceptance=(
                    "Her işaret için sahip, hedef davranış veya kaldırma gerekçesi belirlenmeli.",
                    "Kaldırılan işaretlerin davranışı testle korunmalı.",
                ),
                research_query=self._research_query(profile, "technical debt", "TODO debt management"),
            ))
        return findings

    def _complexity_findings(
        self,
        items: Iterable[ComplexityItem],
        profile: ProjectProfile,
    ) -> list[ImprovementFinding]:
        findings: list[ImprovementFinding] = []
        for item in tuple(items)[:24]:
            if item.risk not in {"medium", "high"}:
                continue
            evidence = ImprovementEvidence(
                source="complexity_analyzer",
                path=item.path,
                line=item.line,
                detail=f"{item.qualified_name} için yüksek kontrol akışı karmaşıklığı",
                metric=(
                    f"cyclomatic={item.cyclomatic}, cognitive={item.cognitive}, "
                    f"nesting={item.max_nesting}, lines={item.line_count}, params={item.parameter_count}"
                ),
            )
            recommendation = (
                "Fonksiyonu sorumluluk sınırlarına göre küçük, adlandırılmış adımlara ayır; "
                "koşul dallarını erken dönüşler veya strateji nesneleriyle sadeleştir."
            )
            if item.kind == "class":
                recommendation = (
                    "Sınıfın birbirinden bağımsız sorumluluklarını belirle ve yalnızca gerçek bağlam sınırlarında "
                    "alt servislere ayır; dış API'yi koru."
                )
            findings.append(self._finding(
                severity="high" if item.risk == "high" else "medium",
                category="complexity_hotspot",
                title=f"Karmaşıklık odağı: {item.qualified_name}",
                explanation="; ".join(item.reasons) or "Karmaşıklık eşikleri aşıldı.",
                confidence=0.94,
                evidence=(evidence,),
                paths=(item.path,),
                recommendation=recommendation,
                acceptance=(
                    "Aynı sembol için bilişsel veya döngüsel karmaşıklık ölçümü düşmeli.",
                    "Dışarıdan kullanılan fonksiyon/sınıf sözleşmesi korunmalı.",
                    "İlgili testler ve proje doğrulaması geçmeli.",
                ),
                research_query=self._research_query(
                    profile,
                    "complexity refactoring",
                    f"{item.kind} cognitive complexity dependency safe refactoring",
                ),
            ))
        return findings

    def _dependency_cycle_findings(
        self,
        graph: DependencyGraph,
        profile: ProjectProfile,
    ) -> list[ImprovementFinding]:
        findings: list[ImprovementFinding] = []
        for component in self._strongly_connected_components(graph)[:12]:
            members = tuple(sorted(component, key=str.casefold))
            evidence: list[ImprovementEvidence] = []
            member_set = set(members)
            for source in members:
                for target in sorted(graph.outgoing.get(source, set())):
                    if target in member_set:
                        evidence.append(ImprovementEvidence(
                            source="dependency_graph",
                            path=source,
                            line=0,
                            detail=f"{source} -> {target}",
                            metric="cycle_edge",
                        ))
                        if len(evidence) >= _MAX_EVIDENCE:
                            break
                if len(evidence) >= _MAX_EVIDENCE:
                    break
            findings.append(self._finding(
                severity="high" if len(members) >= 4 else "medium",
                category="dependency_cycle",
                title=f"Döngüsel dosya bağımlılığı: {len(members)} dosya",
                explanation=(
                    "Dosyalar birbirini dolaylı veya doğrudan içe aktarıyor. Bu durum başlangıç sırası, test izolasyonu "
                    "ve değişiklik etkisi açısından kırılganlık oluşturabilir."
                ),
                confidence=0.88,
                evidence=tuple(evidence),
                paths=members,
                recommendation=(
                    "Döngüde ortak kullanılan sözleşmeyi daha düşük seviyeli bir modüle taşı veya bağımlılık yönünü "
                    "arayüz/protokol üzerinden tek yöne çevir."
                ),
                acceptance=(
                    "Aynı güçlü bağlı bileşen bağımlılık grafiğinde tekrar oluşmamalı.",
                    "Modül import ve başlangıç testleri geçmeli.",
                    "Kamuya açık API ve davranış korunmalı.",
                ),
                research_query=self._research_query(
                    profile,
                    "dependency cycle architecture",
                    "dependency inversion module boundaries official guidance",
                ),
            ))
        return findings

    def _dependency_hotspot_findings(
        self,
        graph: DependencyGraph,
        profile: ProjectProfile,
    ) -> list[ImprovementFinding]:
        nodes = set(graph.outgoing) | set(graph.incoming)
        ranked: list[tuple[int, int, int, str]] = []
        for node in nodes:
            fan_out = len(graph.outgoing.get(node, set()))
            fan_in = len(graph.incoming.get(node, set()))
            if fan_out < 12 and fan_in < 20:
                continue
            ranked.append((fan_out + fan_in, fan_out, fan_in, node))
        ranked.sort(key=lambda row: (-row[0], -row[1], -row[2], row[3].casefold()))

        findings: list[ImprovementFinding] = []
        for _score, fan_out, fan_in, node in ranked[:12]:
            direction = "çok sayıda bileşene bağımlı" if fan_out >= fan_in else "çok sayıda bileşen tarafından kullanılıyor"
            findings.append(self._finding(
                severity="high" if fan_out >= 25 or fan_in >= 40 else "medium",
                category="dependency_hotspot",
                title=f"Bağımlılık odağı: {node}",
                explanation=(
                    f"Bu dosya {direction}; fan-out {fan_out}, fan-in {fan_in}. "
                    "Tek bir değişiklik geniş bir etki alanı oluşturabilir."
                ),
                confidence=0.86,
                evidence=(ImprovementEvidence(
                    source="dependency_graph",
                    path=node,
                    line=0,
                    detail="Yüksek bağlantı sayısı",
                    metric=f"fan_out={fan_out}, fan_in={fan_in}",
                ),),
                paths=(node,),
                recommendation=(
                    "Dosyanın sorumluluklarını kullanım kümelerine göre ölç; kararlı sözleşmeleri ayır ve değişken "
                    "uygulama ayrıntılarını daha küçük bileşenlere taşı."
                ),
                acceptance=(
                    "Dosyanın fan-out veya fan-in değeri ölçülebilir biçimde düşmeli ya da sınırın neden korunacağı belgelenmeli.",
                    "Bağımlı bileşenlerin testleri geçmeli.",
                    "Yeni bir döngüsel bağımlılık oluşmamalı.",
                ),
                research_query=self._research_query(
                    profile,
                    "architectural hotspot high coupling",
                    "modularization stable interfaces dependency management",
                ),
            ))
        return findings

    def _large_file_findings(
        self,
        root: Path,
        profile: ProjectProfile,
    ) -> list[ImprovementFinding]:
        rows: list[tuple[int, str]] = []
        for path in self._iter_source_files(root):
            try:
                size = path.stat().st_size
                relative = path.relative_to(root).as_posix()
            except (OSError, ValueError):
                continue
            if size >= 150_000:
                rows.append((size, relative))
        rows.sort(key=lambda row: (-row[0], row[1].casefold()))
        findings: list[ImprovementFinding] = []
        for size, path in rows[:10]:
            findings.append(self._finding(
                severity="medium" if size < 400_000 else "high",
                category="large_module",
                title=f"Büyük kaynak modülü: {path}",
                explanation=(
                    f"Kaynak dosya {size:,} bayt. Boyut tek başına hata değildir; ancak sorumlulukların, testlerin "
                    "ve değişiklik etkisinin birlikte incelenmesi gereken bir mimari adaydır."
                ),
                confidence=0.78,
                evidence=(ImprovementEvidence(
                    source="filesystem",
                    path=path,
                    line=0,
                    detail="Kaynak dosya boyutu inceleme eşiğini aşıyor",
                    metric=f"bytes={size}",
                ),),
                paths=(path,),
                recommendation=(
                    "Dosyayı yalnızca boyutu nedeniyle bölme. Sınıf/fonksiyon kümeleri, çağrı ilişkileri ve test sınırları "
                    "aynı sorumluluk ayrımını destekliyorsa aşamalı olarak modüllere ayır."
                ),
                acceptance=(
                    "Yeni modül sınırları gerçek sorumluluk ve çağrı kümeleriyle gerekçelendirilmeli.",
                    "Kamuya açık API korunmalı veya açık geçiş katmanı sağlanmalı.",
                    "İlgili testler ve import kontrolleri geçmeli.",
                ),
                research_query=self._research_query(
                    profile,
                    "large module decomposition",
                    "cohesion coupling incremental modularization",
                ),
            ))
        return findings

    def _test_gap_findings(self, profile: ProjectProfile) -> list[ImprovementFinding]:
        if profile.source_files < 10 or profile.test_files > 0:
            return []
        return [self._finding(
            severity="medium",
            category="test_visibility_gap",
            title="Otomatik test dosyası görünmüyor",
            explanation=(
                f"{profile.source_files} kaynak dosyasına karşılık bilinen adlandırmada test dosyası bulunamadı. "
                "Testler farklı bir sistemde olabilir; bu nedenle bulgu orta güvenlidir."
            ),
            confidence=0.66,
            evidence=(ImprovementEvidence(
                source="project_profile",
                path="",
                line=0,
                detail="Kaynak dosyaları bulundu, test dosyası deseni bulunamadı",
                metric=f"source_files={profile.source_files}, test_files=0",
            ),),
            paths=(),
            recommendation="Önce mevcut doğrulama yöntemini doğrula; yoksa kritik davranışlar için küçük, çalıştırılabilir kabul ve regresyon testleri ekle.",
            acceptance=(
                "En az bir otomatik test komutu proje içinden çalıştırılabilmeli.",
                "Kritik kullanıcı akışları için doğrulanabilir testler bulunmalı.",
            ),
            research_query=self._research_query(
                profile,
                "testing strategy",
                "official testing guidance integration regression acceptance tests",
            ),
        )]

    def _profile(self, root: Path) -> tuple[ProjectProfile, int, bool]:
        language_counts: Counter[str] = Counter()
        manifests: list[str] = []
        framework_texts: list[str] = []
        source_files = test_files = scanned = 0
        limit_reached = False
        manifest_names = {
            "pyproject.toml", "requirements.txt", "setup.py", "setup.cfg", "package.json",
            "cargo.toml", "go.mod", "cmakelists.txt", "pom.xml", "build.gradle",
        }
        for path in self._iter_files(root):
            scanned += 1
            if scanned > _MAX_SCAN_FILES:
                limit_reached = True
                break
            try:
                relative = path.relative_to(root).as_posix()
            except ValueError:
                continue
            suffix = path.suffix.casefold()
            if suffix in _SOURCE_SUFFIXES:
                source_files += 1
                language_counts[_LANGUAGE_LABELS.get(suffix, suffix.lstrip(".").upper())] += 1
                name = path.name.casefold()
                rel_fold = relative.casefold()
                if (
                    name.startswith("test_")
                    or name.endswith(("_test.py", ".test.js", ".test.ts", ".spec.js", ".spec.ts"))
                    or any(part in {"test", "tests", "spec", "specs"} for part in Path(rel_fold).parts[:-1])
                ):
                    test_files += 1
            if path.name.casefold() in manifest_names or suffix in {".sln", ".csproj"}:
                manifests.append(relative)
                text = self._read_limited(path)
                if text:
                    framework_texts.append(text.casefold())

        combined = "\n".join(framework_texts)
        frameworks: list[str] = []
        markers = (
            ("pyside6", "PySide6"), ("pyqt6", "PyQt6"), ("fastapi", "FastAPI"),
            ("django", "Django"), ("flask", "Flask"), ("pytest", "pytest"),
            ("electron", "Electron"), ("react", "React"), ("next", "Next.js"),
            ("vue", "Vue"), ("svelte", "Svelte"), ("express", "Express"),
            ("cmake", "CMake"), ("qt", "Qt"), ("cargo", "Cargo"),
        )
        for token, label in markers:
            if re.search(rf"(?<![a-z0-9_]){re.escape(token)}(?![a-z0-9_])", combined):
                frameworks.append(label)
        languages = tuple(sorted(language_counts.items(), key=lambda row: (-row[1], row[0].casefold())))
        return (
            ProjectProfile(
                languages=languages,
                frameworks=tuple(dict.fromkeys(frameworks)),
                manifests=tuple(sorted(set(manifests), key=str.casefold)),
                source_files=source_files,
                test_files=test_files,
            ),
            min(scanned, _MAX_SCAN_FILES),
            limit_reached,
        )

    @staticmethod
    def _strongly_connected_components(graph: DependencyGraph) -> list[set[str]]:
        nodes = sorted(set(graph.outgoing) | set(graph.incoming), key=str.casefold)
        index = 0
        stack: list[str] = []
        on_stack: set[str] = set()
        indices: dict[str, int] = {}
        lowlinks: dict[str, int] = {}
        components: list[set[str]] = []

        def visit(node: str) -> None:
            nonlocal index
            indices[node] = index
            lowlinks[node] = index
            index += 1
            stack.append(node)
            on_stack.add(node)
            for target in sorted(graph.outgoing.get(node, set()), key=str.casefold):
                if target not in indices:
                    visit(target)
                    lowlinks[node] = min(lowlinks[node], lowlinks[target])
                elif target in on_stack:
                    lowlinks[node] = min(lowlinks[node], indices[target])
            if lowlinks[node] != indices[node]:
                return
            component: set[str] = set()
            while stack:
                member = stack.pop()
                on_stack.discard(member)
                component.add(member)
                if member == node:
                    break
            if len(component) > 1:
                components.append(component)

        for node in nodes:
            if node not in indices:
                visit(node)
        components.sort(key=lambda item: (-len(item), tuple(sorted(item, key=str.casefold))))
        return components

    @classmethod
    def _finding(
        cls,
        *,
        severity: str,
        category: str,
        title: str,
        explanation: str,
        confidence: float,
        evidence: tuple[ImprovementEvidence, ...],
        paths: tuple[str, ...],
        recommendation: str,
        acceptance: tuple[str, ...],
        research_query: str,
    ) -> ImprovementFinding:
        clean_paths = tuple(dict.fromkeys(path.replace("\\", "/") for path in paths if path))
        digest = hashlib.sha256()
        for value in (category, title, *clean_paths):
            digest.update(str(value).encode("utf-8", errors="replace"))
            digest.update(b"\0")
        finding_id = "ARC-" + digest.hexdigest()[:10].upper()
        return ImprovementFinding(
            finding_id=finding_id,
            severity=severity if severity in _SEVERITY_ORDER else "medium",
            category=category[:120],
            title=title[:500],
            explanation=explanation[:2000],
            confidence=max(0.0, min(float(confidence), 1.0)),
            evidence=tuple(evidence[:_MAX_EVIDENCE]),
            affected_paths=clean_paths,
            recommendation=recommendation[:3000],
            acceptance_criteria=tuple(item[:1000] for item in acceptance[:12] if item),
            research_query=research_query[:1000],
        )

    @staticmethod
    def _evidence_from_review(issue: CodeReviewIssue) -> ImprovementEvidence:
        return ImprovementEvidence(
            source="code_review",
            path=issue.path,
            line=issue.line,
            detail=issue.message,
            metric=issue.kind,
        )

    @staticmethod
    def _research_query(profile: ProjectProfile, category: str, detail: str) -> str:
        stack = profile.stack_text
        clean_detail = re.sub(r"\s+", " ", str(detail)).strip()[:220]
        clean_category = re.sub(r"\s+", " ", str(category)).strip()[:120]
        return (
            f"{stack} {clean_category} {clean_detail} official documentation architecture best practices "
            "migration testing"
        ).strip()

    @staticmethod
    def _is_test_path(path: str) -> bool:
        normalized = str(path or "").replace("\\", "/").casefold()
        name = normalized.rsplit("/", 1)[-1]
        return (
            normalized.startswith(("tests/", "test/", "spec/", "specs/"))
            or "/tests/" in f"/{normalized}"
            or name.startswith("test_")
            or name.endswith(("_test.py", ".test.js", ".test.ts", ".spec.js", ".spec.ts"))
        )

    @staticmethod
    def _deduplicate_and_rank(findings: Iterable[ImprovementFinding]) -> list[ImprovementFinding]:
        by_id: dict[str, ImprovementFinding] = {}
        for finding in findings:
            previous = by_id.get(finding.finding_id)
            if previous is None or (
                _SEVERITY_ORDER[finding.severity], -finding.confidence
            ) < (
                _SEVERITY_ORDER[previous.severity], -previous.confidence
            ):
                by_id[finding.finding_id] = finding
        return sorted(
            by_id.values(),
            key=lambda item: (
                _SEVERITY_ORDER.get(item.severity, 9),
                -item.confidence,
                item.category.casefold(),
                item.title.casefold(),
                item.finding_id,
            ),
        )

    @classmethod
    def _iter_source_files(cls, root: Path) -> Iterator[Path]:
        for path in cls._iter_files(root):
            if path.suffix.casefold() in _SOURCE_SUFFIXES:
                yield path

    @staticmethod
    def _iter_files(root: Path) -> Iterator[Path]:
        count = 0
        try:
            iterator = root.rglob("*")
            for path in iterator:
                count += 1
                if count > _MAX_SCAN_FILES * 2:
                    break
                try:
                    if path.is_symlink() or not path.is_file():
                        continue
                    relative = path.relative_to(root)
                    if any(part in IGNORED_DIRS or part == ".artmach_assistant" for part in relative.parts[:-1]):
                        continue
                except (OSError, RuntimeError, ValueError):
                    continue
                yield path
        except (OSError, RuntimeError):
            return

    @staticmethod
    def _read_limited(path: Path) -> str:
        try:
            if path.is_symlink() or path.stat().st_size > _MAX_TEXT_BYTES:
                return ""
            return path.read_text(encoding="utf-8", errors="replace")[:_MAX_TEXT_BYTES]
        except (OSError, UnicodeError):
            return ""
