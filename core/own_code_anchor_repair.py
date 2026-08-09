from pathlib import Path
import ast
import copy
import difflib
import re
import textwrap
from typing import Any

from artmach_assistant.core.workspace import WorkspaceError


_SYMBOL_PATTERN = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*)(?:\.([A-Za-z_][A-Za-z0-9_]*)){1,2}\b"
)
_DOTTED_SYMBOL_PATTERN = re.compile(
    r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){1,2}\b"
)


def _requested_symbol(instruction: str) -> tuple[str, str] | None:
    """Return the approved direct Class.method target from an instruction.

    Runtime locations may include a nested callable suffix such as
    ``TaskOrchestrator.wrap.execute``. Structural edit operations can only
    target direct class methods, so the approved target is the outer
    ``TaskOrchestrator.wrap`` method. Prefer that explicit three-part source
    location over earlier human-facing labels such as
    ``TaskOrchestrator.execute_task``.
    """
    text = str(instruction or "")

    explicit = re.search(
        r"(?im)^APPROVED_STRUCTURAL_TARGET:\s*"
        r"([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\s*$",
        text,
    )
    if explicit is not None:
        return explicit.group(1), explicit.group(2)

    ignored_suffixes = {
        "py", "pyw", "json", "toml", "yaml", "yml", "md", "txt",
        "ini", "cfg", "bat", "cmd", "ps1", "sh", "html", "css",
    }
    symbols = [match.group(0) for match in _DOTTED_SYMBOL_PATTERN.finditer(text)]

    for symbol in reversed(symbols):
        parts = symbol.split(".")
        if parts[-1].casefold() in ignored_suffixes:
            continue
        if len(parts) == 3 and parts[0][:1].isupper():
            return parts[0], parts[1]

    for symbol in symbols:
        parts = symbol.split(".")
        if parts[-1].casefold() in ignored_suffixes:
            continue
        if len(parts) >= 2 and parts[0][:1].isupper():
            return parts[0], parts[1]

    for symbol in symbols:
        parts = symbol.split(".")
        if len(parts) >= 2 and parts[-1].casefold() not in ignored_suffixes:
            return parts[0], parts[1]
    return None


def _symbol_source(
    source: str,
    *,
    class_name: str,
    method_name: str,
) -> str:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ""

    lines = source.splitlines(keepends=True)

    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue

        for child in node.body:
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if child.name != method_name:
                continue

            start = max(0, int(child.lineno) - 1)
            end = int(getattr(child, "end_lineno", child.lineno))
            return "".join(lines[start:end])

    return ""


def _unique_class_method_source_for_anchor(
    source: str,
    *,
    class_name: str,
    anchor: str,
    replacement: str = "",
) -> str:
    """Return one direct class method that safely owns an ambiguous anchor.

    Prefer a sole method containing the anchor.  If several methods contain
    it, use identifiers referenced by the replacement only as a scope proof:
    the selected method must have a unique highest overlap with its declared
    parameters.  This resolves ``task_id, token, action`` to ``wrap`` without
    guessing from a human-facing operation label such as ``execute_task``.
    """
    if not anchor:
        return ""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ""

    replacement_names = set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", replacement))
    lines = source.splitlines(keepends=True)
    candidates: list[tuple[str, int]] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        for child in node.body:
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            start = max(0, int(child.lineno) - 1)
            end = int(getattr(child, "end_lineno", child.lineno))
            method_source = "".join(lines[start:end])
            if method_source.count(anchor) != 1:
                continue
            argument_names = {row.arg for row in child.args.args}
            argument_names.update(row.arg for row in child.args.kwonlyargs)
            if child.args.vararg is not None:
                argument_names.add(child.args.vararg.arg)
            if child.args.kwarg is not None:
                argument_names.add(child.args.kwarg.arg)
            argument_names.discard("self")
            score = len(argument_names & replacement_names)
            candidates.append((method_source, score))
        break

    if len(candidates) == 1:
        return candidates[0][0]
    if not candidates:
        return ""

    best_score = max(score for _source, score in candidates)
    if best_score <= 0:
        return ""
    best = [method_source for method_source, score in candidates if score == best_score]
    return best[0] if len(best) == 1 else ""


def normalize_structural_class_method_insertions(
    payload: dict[str, Any],
    *,
    project_root: Path,
    instruction: str,
) -> dict[str, Any]:
    """Convert AST-scoped class-method additions into exact text operations.

    A model must not guess a repeated ``def run`` anchor to add a sibling
    method.  ``insert_class_method`` names the owning class instead; this
    normalizer proves that class in the current file, validates a complete
    method body, fixes its class indentation, and chooses an exact structural
    boundary outside the target method.
    """
    repaired = copy.deepcopy(payload)
    files = repaired.get("files")
    if not isinstance(files, list):
        return repaired

    root = Path(project_root).resolve(strict=False)
    requested = _requested_symbol(instruction)
    for file_row in files:
        if not isinstance(file_row, dict):
            continue
        operations = file_row.get("operations")
        if not isinstance(operations, list):
            continue
        raw_path = str(file_row.get("path", "")).strip()
        candidate = (root / raw_path).resolve(strict=False)
        try:
            candidate.relative_to(root)
            source = candidate.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=raw_path)
        except (ValueError, OSError, UnicodeError, SyntaxError):
            continue
        lines = source.splitlines(keepends=True)

        for operation_index, operation in enumerate(operations, start=1):
            if not isinstance(operation, dict):
                continue
            kind = str(operation.get("op", "")).strip().casefold()
            if kind != "insert_class_method":
                continue
            class_name = str(operation.get("class_name", "")).strip()
            content = operation.get("content")
            if requested and class_name != requested[0]:
                # Small code models sometimes concatenate the approved class and
                # method scope into ``class_name`` for insert_class_method, for
                # example ``TaskOrchestrator.wrap``. This operation only accepts
                # an owning class. Normalize the exact approved Class.method
                # spelling deterministically; reject every other scope change.
                approved_method_scope = f"{requested[0]}.{requested[1]}"
                if class_name == approved_method_scope:
                    class_name = requested[0]
                    operation["class_name"] = class_name
                else:
                    raise WorkspaceError(
                        f"Yapısal metot hedef sınıfı onaylı sembolle eşleşmiyor: "
                        f"{raw_path} işlem {operation_index}; beklenen={requested[0]}"
                    )
            owners = [
                node for node in tree.body
                if isinstance(node, ast.ClassDef) and node.name == class_name
            ]
            if len(owners) != 1:
                raise WorkspaceError(
                    f"Yapısal metot hedef sınıfı tam olarak bir kez bulunmalı: "
                    f"{raw_path} işlem {operation_index}; bulunan={len(owners)}"
                )
            if not isinstance(content, str) or not content.strip():
                raise WorkspaceError(
                    f"insert_class_method için eksiksiz content gerekli: "
                    f"{raw_path} işlem {operation_index}"
                )

            dedented = textwrap.dedent(content).strip("\r\n") + "\n"
            try:
                parsed = ast.parse(dedented)
            except SyntaxError as exc:
                raise WorkspaceError(
                    f"Yardımcı metot eksik veya sözdizimi geçersiz: "
                    f"{raw_path} işlem {operation_index}; {exc.msg}"
                ) from exc
            if (
                len(parsed.body) != 1
                or not isinstance(parsed.body[0], (ast.FunctionDef, ast.AsyncFunctionDef))
                or not parsed.body[0].body
                or all(isinstance(row, ast.Pass) for row in parsed.body[0].body)
            ):
                raise WorkspaceError(
                    f"Yardımcı metot content alanı gövdeli tek bir metot olmalı: "
                    f"{raw_path} işlem {operation_index}"
                )
            method_name = parsed.body[0].name
            owner = owners[0]
            if any(
                isinstance(row, (ast.FunctionDef, ast.AsyncFunctionDef))
                and row.name == method_name
                for row in owner.body
            ):
                raise WorkspaceError(
                    f"Yardımcı metot sınıfta zaten var: {raw_path} işlem "
                    f"{operation_index}; {class_name}.{method_name}"
                )

            owner_index = tree.body.index(owner)
            rendered = textwrap.indent(dedented, "    ") + "\n"

            if owner_index + 1 < len(tree.body):
                following = tree.body[owner_index + 1]
                decorator_lines = [
                    int(getattr(row, "lineno", 0))
                    for row in getattr(following, "decorator_list", ())
                    if int(getattr(row, "lineno", 0)) > 0
                ]
                anchor_line = min(
                    [int(getattr(following, "lineno", 0)), *decorator_lines]
                )
                if not anchor_line or anchor_line > len(lines):
                    raise WorkspaceError(
                        f"Yapisal metot ekleme siniri cozumlenemedi: "
                        f"{raw_path} islem {operation_index}"
                    )
                anchor = lines[anchor_line - 1]
                operation_kind = "insert_before"
                operation_content = rendered
            else:
                if not owner.body:
                    raise WorkspaceError(
                        f"Yapisal metot hedef sinifi bos: "
                        f"{raw_path} islem {operation_index}"
                    )

                last_member = owner.body[-1]
                member_start = int(getattr(last_member, "lineno", 0))
                member_end = int(
                    getattr(last_member, "end_lineno", 0)
                )

                decorator_lines = [
                    int(getattr(row, "lineno", 0))
                    for row in getattr(last_member, "decorator_list", ())
                    if int(getattr(row, "lineno", 0)) > 0
                ]
                if decorator_lines:
                    member_start = min(
                        [member_start, *decorator_lines]
                    )

                if (
                    not member_start
                    or not member_end
                    or member_start > member_end
                    or member_end > len(lines)
                ):
                    raise WorkspaceError(
                        f"Yapisal metot sinif sonu siniri cozumlenemedi: "
                        f"{raw_path} islem {operation_index}"
                    )

                anchor = "".join(
                    lines[member_start - 1:member_end]
                )
                operation_kind = "insert_after"
                operation_content = "\n" + rendered

            if source.count(anchor) != 1:
                raise WorkspaceError(
                    f"Yapisal metot sinif sonu siniri benzersiz degil: "
                    f"{raw_path} islem {operation_index}; "
                    f"bulunan={source.count(anchor)}"
                )

            operation.clear()
            operation.update({
                "op": operation_kind,
                "anchor": anchor,
                "content": operation_content,
                "_structural_method": method_name,
            })
    return repaired


def _expression_fingerprint(value: str) -> str:
    try:
        parsed = ast.parse(str(value or "").strip(), mode="eval")
    except SyntaxError:
        return ""
    return ast.dump(parsed.body, annotate_fields=True, include_attributes=False)


def _normalize_if_test_selector(value: str) -> str:
    """Accept either an if-test expression or one bare ``if ...:`` header.

    Code models sometimes preserve the source line's ``if`` keyword and colon
    even though ``block_test`` is documented as an expression.  Stripping them
    textually would be unsafe for multiline conditions, so parse the header as
    a real statement and recover its AST test only when it is exactly one
    body-less ``if`` header after adding a temporary body.
    """
    selector = str(value or "").strip()
    if _expression_fingerprint(selector):
        return selector
    statement_header = selector
    if selector.casefold().startswith("if ") and not selector.rstrip().endswith(":"):
        statement_header = selector.rstrip() + ":"
    try:
        parsed = ast.parse(statement_header + "\n    pass\n")
    except SyntaxError:
        return selector
    if len(parsed.body) != 1 or not isinstance(parsed.body[0], ast.If):
        return selector
    statement = parsed.body[0]
    if statement.orelse or len(statement.body) != 1 or not isinstance(statement.body[0], ast.Pass):
        return selector
    return ast.unparse(statement.test)


def normalize_structural_method_block_replacements(
    payload: dict[str, Any],
    *,
    project_root: Path,
    instruction: str,
) -> dict[str, Any]:
    """Resolve a complete direct method-body statement through Python's AST.

    ``replace_method_block`` avoids copying a large, indentation-sensitive
    source fragment into JSON.  The model names the approved class/method and
    the condition of one direct ``if`` statement.  This normalizer proves that
    selector is unique, replaces the *entire* AST node, and then emits the
    ordinary exact replace operation consumed by EditManager.
    """
    repaired = copy.deepcopy(payload)
    files = repaired.get("files")
    if not isinstance(files, list):
        return repaired

    requested = _requested_symbol(instruction)
    root = Path(project_root).resolve(strict=False)
    normalized_instruction = str(instruction or "").casefold()
    active_dialogue_request = (
        requested == ("WakeWordWorker", "run")
        and any(word in normalized_instruction for word in ("aktif diyalog", "active dialogue"))
    )

    for file_row in files:
        if not isinstance(file_row, dict):
            continue
        operations = file_row.get("operations")
        if not isinstance(operations, list):
            continue
        raw_path = str(file_row.get("path", "")).strip()
        candidate = (root / raw_path).resolve(strict=False)
        try:
            candidate.relative_to(root)
            source = candidate.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=raw_path)
        except (ValueError, OSError, UnicodeError, SyntaxError):
            continue
        lines = source.splitlines(keepends=True)

        for operation_index, operation in enumerate(operations, start=1):
            if not isinstance(operation, dict):
                continue
            if str(operation.get("op", "")).strip().casefold() != "replace_method_block":
                continue
            class_name = str(operation.get("class_name", "")).strip()
            method_name = str(operation.get("method_name", "")).strip()
            nested_runtime_target = False
            if requested:
                approved_class, approved_method = requested
                approved_scope = f"{approved_class}.{approved_method}"

                if class_name == approved_scope and method_name:
                    # Runtime locations can name an inner callable as
                    # ``TaskOrchestrator.wrap.execute``. Structural operations
                    # cannot target nested functions through class_name/method_name.
                    # Treat this malformed pair as an exact statement edit inside
                    # the approved outer method; ambiguous-anchor repair will add
                    # the required source context without guessing.
                    class_name = approved_class
                    method_name = approved_method
                    nested_runtime_target = True
                    operation["class_name"] = class_name
                    operation["method_name"] = method_name
                elif class_name == approved_class and "." in method_name:
                    outer_method = method_name.split(".", 1)[0].strip()
                    if outer_method == approved_method:
                        method_name = outer_method
                        operation["method_name"] = method_name

            block_test = _normalize_if_test_selector(operation.get("block_test", ""))
            replacement = operation.get("replacement")

            if nested_runtime_target and isinstance(replacement, str):
                plain_statement = str(block_test or "").strip().rstrip(";")
                if plain_statement:
                    operation.clear()
                    operation.update({
                        "op": "replace",
                        "old": plain_statement,
                        "new": replacement.strip(),
                    })
                    continue

            # Small code models sometimes use replace_method_block for a plain
            # standalone call and also target a human-facing runtime label such
            # as execute_task instead of the approved direct method wrap.  Only
            # downgrade that malformed structural operation to an exact replace
            # when the same payload proves an equivalent helper extraction:
            # the replacement is one direct self.<helper>(...) call and the
            # inserted helper contains exactly the original statement.  Normal
            # ambiguous-anchor repair will then scope and expand the replace.
            helper_name = (
                _direct_self_helper_name(str(replacement or ""))
                if isinstance(replacement, str)
                else ""
            )
            helper_statement = (
                _inserted_helper_single_statement(operations, helper_name)
                if helper_name
                else ""
            )
            plain_statement = str(block_test or "").strip().rstrip(";")
            if (
                requested
                and class_name == requested[0]
                and method_name != requested[1]
                and helper_statement
                and helper_statement == plain_statement
            ):
                operation.clear()
                operation.update({
                    "op": "replace",
                    "old": plain_statement,
                    "new": str(replacement).strip(),
                })
                continue

            if requested and (class_name, method_name) != requested:
                raise WorkspaceError(
                    "Yapısal blok hedefi onaylı sembolle eşleşmiyor: "
                    f"{raw_path} işlem {operation_index}; beklenen={requested[0]}.{requested[1]}"
                )
            wanted = _expression_fingerprint(block_test)
            if not wanted:
                raise WorkspaceError(
                    f"replace_method_block için geçerli block_test gerekli: "
                    f"{raw_path} işlem {operation_index}"
                )
            methods: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
            for owner in tree.body:
                if not isinstance(owner, ast.ClassDef) or owner.name != class_name:
                    continue
                methods.extend(
                    row for row in owner.body
                    if isinstance(row, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and row.name == method_name
                )
            if len(methods) != 1:
                raise WorkspaceError(
                    "Yapısal blok hedef metodu tam olarak bir kez bulunmalı: "
                    f"{raw_path} işlem {operation_index}; bulunan={len(methods)}"
                )
            matches = [
                row for row in ast.walk(methods[0])
                if isinstance(row, ast.If)
                and ast.dump(row.test, annotate_fields=True, include_attributes=False) == wanted
            ]
            if len(matches) != 1:
                raise WorkspaceError(
                    "Yapısal blok koşulu hedef metot ağacında tam olarak "
                    f"bir kez bulunmalı: {raw_path} işlem {operation_index}; bulunan={len(matches)}"
                )
            if active_dialogue_request:
                selector_attrs = {row.attr for row in ast.walk(matches[0].test) if isinstance(row, ast.Attribute)}
                selector_constants = {row.value for row in ast.walk(matches[0].test) if isinstance(row, ast.Constant)}
                if "_next_mode" not in selector_attrs or "sleep" not in selector_constants:
                    raise WorkspaceError(
                        "Aktif diyalog çıkarımı yalnız WakeWordWorker.run içindeki "
                        "`self._next_mode != \"sleep\"` tam üst düzey bloğunu hedeflemeli; "
                        f"daha küçük alt blok reddedildi: {raw_path} işlem {operation_index}"
                    )
            if not isinstance(replacement, str) or not replacement.strip():
                raise WorkspaceError(
                    f"replace_method_block için replacement gerekli: {raw_path} işlem {operation_index}"
                )
            dedented_replacement = textwrap.dedent(replacement).strip("\r\n") + "\n"
            replacement_lines = dedented_replacement.splitlines()
            replacement_if_tests = []
            try:
                partial_tree = ast.parse(dedented_replacement)
            except SyntaxError:
                partial_tree = None
            if partial_tree is not None:
                replacement_if_tests = [
                    ast.dump(row.test, annotate_fields=True, include_attributes=False)
                    for row in ast.walk(partial_tree)
                    if isinstance(row, ast.If)
                ]
            selector_was_copied = wanted in replacement_if_tests
            if (
                selector_was_copied
                or len(replacement_lines) > 12
                or dedented_replacement.lstrip().startswith((
                    "if self._next_mode", "if (self._next_mode",
                ))
            ):
                raise WorkspaceError(
                    "replace_method_block replacement alanına çıkarılan eski bloğu "
                    "yeniden kopyalama. Bu alan en fazla 12 satırlık yalnız "
                    "`self.<yardımcı_metot>(...)` çağrısını ve gerekiyorsa run "
                    "içindeki break/continue kararını içermeli; seçilen AST bloğu "
                    "Jarvis tarafından otomatik kaldırılır. Tam davranış yardımcı "
                    f"metodun content alanına taşınmalı: {raw_path} işlem {operation_index}"
                )
            try:
                replacement_tree = ast.parse(dedented_replacement)
            except SyntaxError as exc:
                raise WorkspaceError(
                    f"Yapısal blok replacement sözdizimi geçersiz: {raw_path} "
                    f"işlem {operation_index}; {exc.msg}"
                ) from exc
            self_calls = [
                node for node in ast.walk(replacement_tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "self"
            ]
            if not self_calls:
                raise WorkspaceError(
                    "Çıkarılan blok replacement alanında `self.<yardımcı_metot>(...)` "
                    f"çağrısı zorunlu: {raw_path} işlem {operation_index}"
                )
            first_statement = replacement_tree.body[0] if replacement_tree.body else None
            first_value: ast.AST | None = None
            if isinstance(first_statement, ast.Expr):
                first_value = first_statement.value
            elif isinstance(first_statement, (ast.Assign, ast.AnnAssign)):
                first_value = first_statement.value
            direct_self_call = (
                isinstance(first_value, ast.Call)
                and isinstance(first_value.func, ast.Attribute)
                and isinstance(first_value.func.value, ast.Name)
                and first_value.func.value.id == "self"
            )
            if not direct_self_call:
                raise WorkspaceError(
                    "replace_method_block replacement alanında yardımcı çağrı "
                    "başka bir if/while/try bloğuna sarılmamalı. Seçilen if düğümü "
                    "AST tarafından bütünüyle kaldırılır; replacement doğrudan "
                    "`self.<yardımcı_metot>(...)` çağrısıyla başlamalı. Örnek: "
                    '"replacement": "self._listen_active_dialogue()". Tam davranış '
                    "ve gerekli girdiler yardımcı metodun content alanında olmalı: "
                    f"{raw_path} işlem {operation_index}"
                )
            target = matches[0]
            start = int(target.lineno) - 1
            end = int(getattr(target, "end_lineno", target.lineno))
            old = "".join(lines[start:end])
            indent = re.match(r"[ \t]*", lines[start]).group(0)
            new = textwrap.indent(dedented_replacement, indent)
            operation.clear()
            operation.update({
                "op": "replace",
                "old": old,
                "new": new,
                "_structural_block": f"{class_name}.{method_name}:{block_test}",
            })
    return repaired


def build_structural_method_block_guidance(
    *,
    project_root: Path,
    instruction: str,
) -> str:
    """Return the complete proven extraction range for a retry prompt."""
    requested = _requested_symbol(instruction)
    normalized = str(instruction or "").casefold()
    if requested != ("WakeWordWorker", "run") or not any(
        word in normalized for word in ("aktif diyalog", "active dialogue")
    ):
        return ""
    candidate = (Path(project_root).resolve(strict=False) / "app.py").resolve(strict=False)
    try:
        candidate.relative_to(Path(project_root).resolve(strict=False))
        source = candidate.read_text(encoding="utf-8")
        tree = ast.parse(source, filename="app.py")
    except (ValueError, OSError, UnicodeError, SyntaxError):
        return ""
    lines = source.splitlines(keepends=True)
    matches: list[ast.If] = []
    for owner in tree.body:
        if not isinstance(owner, ast.ClassDef) or owner.name != requested[0]:
            continue
        for method in owner.body:
            if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) or method.name != requested[1]:
                continue
            for row in ast.walk(method):
                if not isinstance(row, ast.If):
                    continue
                attrs = {node.attr for node in ast.walk(row.test) if isinstance(node, ast.Attribute)}
                constants = {node.value for node in ast.walk(row.test) if isinstance(node, ast.Constant)}
                if "_next_mode" in attrs and "sleep" in constants:
                    matches.append(row)
    if len(matches) != 1:
        return ""
    target = matches[0]
    complete = "".join(
        lines[int(target.lineno) - 1:int(getattr(target, "end_lineno", target.lineno))]
    )
    return (
        "YAPISAL CIKARMA ICIN TAM GERCEK KAYNAK ARALIGI "
        "(ilk satirdan son satira kadar tek AST blogu):\n"
        + complete
        + "\nBu metni ham replace old alanina kopyalama. "
        "replace_method_block kullan: class_name=WakeWordWorker, method_name=run, "
        "block_test=self._next_mode != \"sleep\". Secilen 75 satirlik eski blok "
        "AST'den otomatik kaldirilir; replacement alanina bu blogu yeniden yazma. "
        "replacement yalniz self.<yardimci_metot>(...) cagrisini ve gerekiyorsa "
        "run icindeki break/continue kararini iceren en fazla 12 satir olmali. "
        "Bu blokta InterruptedError gercek run dongusunu bitirdigi icin helper "
        "bir karar sonucu dondurmek ZORUNDADIR. Somut guvenli sekil: "
        "`dialogue_action = self._listen_active_dialogue()`; ardindan "
        "`if dialogue_action == \"break\": break`; diger tum eski yollar icin "
        "`continue`. Helper icindeki eski `continue` yollarini erken bir karar "
        "donusune cevir; fakat bu yollardan once calisan `_next_mode` atamalarini, "
        "`self.msleep(...)` cagrisini, sinyal yayimlarini ve durum guncellemelerini "
        "aynen koru. Helper'in InterruptedError yolu `\"break\"`, diger tamamlanan "
        "yollari `\"continue\"` dondurmelidir. "
        "Cagriyi block_test kosuluyla yeniden if icine sarma. replacement kesin "
        "olarak su kontrol akisina denk olmali: "
        "`dialogue_action = self._listen_active_dialogue()`; "
        "`if dialogue_action == \"break\": break`; `continue`. Tek satirlik "
        "`self._listen_active_dialogue()` replacement'i kullanma; bu sekil dis "
        "dongunun break/continue davranisini kaybeder. Kaldirilan blok icinde "
        "uretilen command/mode gibi yerel degiskenleri replacement cagrisina "
        "arguman verme; bu girdileri yardimci metodun kendisi uretmeli. "
        "Tasinan davranisin tamami insert_class_method content alaninda bulunmali."
    )


def validate_behavior_preserving_extraction_payload(
    payload: dict[str, Any],
    *,
    instruction: str,
) -> None:
    """Require both halves of a behavior-preserving method extraction."""
    normalized = str(instruction or "").casefold()
    if not (
        ("davranışı değiştirmeden" in normalized or "davranisi degistirmeden" in normalized)
        and any(word in normalized for word in ("çıkar", "cikar", "ayır", "ayir", "extract"))
    ):
        return

    files = payload.get("files")
    if not isinstance(files, list):
        return
    structural: list[tuple[str, int]] = []
    replacements: list[dict[str, Any]] = []
    for file_row in files:
        if not isinstance(file_row, dict):
            continue
        for index, operation in enumerate(file_row.get("operations", ()), start=1):
            if not isinstance(operation, dict):
                continue
            method_name = str(operation.get("_structural_method", "")).strip()
            if method_name:
                structural.append((method_name, index))
            if str(operation.get("op", "")).strip().casefold() in {"replace", "replace_exact"}:
                replacements.append(operation)

    if not structural:
        raise WorkspaceError(
            "Davranış-koruyan çıkarma için insert_class_method operasyonu zorunlu; "
            "ham def bildirimiyle insert_before/insert_after kullanma."
        )
    active_dialogue_request = (
        _requested_symbol(instruction) == ("WakeWordWorker", "run")
        and any(word in normalized for word in ("aktif diyalog", "active dialogue"))
    )
    if active_dialogue_request and not any(
        str(row.get("_structural_block", "")).startswith("WakeWordWorker.run:")
        for row in replacements
    ):
        raise WorkspaceError(
            "Aktif diyalog çıkarımında ham replace yasak; tam kaynak aralığı için "
            "replace_method_block operasyonu zorunlu. block_test olarak "
            "`self._next_mode != \"sleep\"` kullan."
        )
    for method_name, _index in structural:
        call_pattern = re.compile(rf"\bself\.{re.escape(method_name)}\s*\(")
        if not any(
            isinstance(row.get("old"), str)
            and isinstance(row.get("new"), str)
            and row.get("old") != row.get("new")
            and call_pattern.search(str(row.get("new")))
            for row in replacements
        ):
            raise WorkspaceError(
                "Davranış-koruyan çıkarma iki gerçek operasyon gerektirir: "
                f"{method_name} yardımcı metodunu ekle ve run içindeki eski bloğu "
                "bu metot çağrısıyla ayrı bir replace işleminde değiştir."
            )


def _expand_unique_replace(
    source: str,
    symbol_source: str,
    old: str,
    new: str,
) -> tuple[str, str] | None:
    if not old or source.count(old) <= 1:
        return None

    positions = _occurrence_positions(symbol_source, old)
    if len(positions) != 1:
        # Indented Python text can be counted as a substring of a more deeply
        # indented line.  When exactly one occurrence starts at a real line
        # boundary, that is the only position which can represent the model's
        # complete source line; the others are indentation substrings.
        line_aligned = tuple(
            position
            for position in positions
            if position == 0 or symbol_source[position - 1] in "\r\n"
        )
        if len(line_aligned) != 1:
            return None
        position = line_aligned[0]
    else:
        position = positions[0]

    if position < 0:
        return None

    before = symbol_source[:position]
    after = symbol_source[position + len(old):]

    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)

    for radius in range(1, 13):
        prefix = "".join(before_lines[-radius:])
        suffix = "".join(after_lines[:radius])

        expanded_old = prefix + old + suffix
        if source.count(expanded_old) != 1:
            continue

        expanded_new = prefix + new + suffix
        return expanded_old, expanded_new

    return None


def _expand_unique_insert_anchor(
    source: str,
    symbol_source: str,
    anchor: str,
    *,
    insert_before: bool,
) -> str:
    """Disambiguate an insert anchor without moving the insertion point."""
    if not anchor or source.count(anchor) <= 1:
        return ""

    if symbol_source.count(anchor) != 1:
        return ""

    position = symbol_source.find(anchor)
    if position < 0:
        return ""

    before = symbol_source[:position].splitlines(keepends=True)
    after = symbol_source[position + len(anchor):].splitlines(keepends=True)

    # insert_before(anchor) may only grow to the right: content +
    # (anchor + suffix) keeps content immediately before the original anchor.
    # insert_after(anchor) has the inverse rule and may only grow to the left.
    for radius in range(1, 17):
        if insert_before:
            expanded = anchor + "".join(after[:radius])
        else:
            expanded = "".join(before[-radius:]) + anchor

        if source.count(expanded) == 1:
            return expanded

    return ""



def _direct_self_helper_name(value: str) -> str:
    """Return helper name for one direct ``self.helper(...)`` expression."""
    try:
        tree = ast.parse(textwrap.dedent(str(value or "")).strip())
    except SyntaxError:
        return ""
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Expr):
        return ""
    call = tree.body[0].value
    if (
        not isinstance(call, ast.Call)
        or not isinstance(call.func, ast.Attribute)
        or not isinstance(call.func.value, ast.Name)
        or call.func.value.id != "self"
    ):
        return ""
    return call.func.attr


def _inserted_helper_single_statement(
    operations: list[dict[str, Any]],
    helper_name: str,
) -> str:
    """Return the sole helper-body statement inserted by this proposal."""
    for operation in operations:
        if str(operation.get("_structural_method", "")).strip() != helper_name:
            continue
        content = operation.get("content")
        if not isinstance(content, str):
            continue
        try:
            tree = ast.parse(textwrap.dedent(content))
        except SyntaxError:
            continue
        methods = [
            row for row in ast.walk(tree)
            if isinstance(row, (ast.FunctionDef, ast.AsyncFunctionDef))
            and row.name == helper_name
        ]
        if len(methods) != 1 or len(methods[0].body) != 1:
            continue
        statement = methods[0].body[0]
        if not isinstance(statement, ast.Expr):
            continue
        return ast.unparse(statement.value)
    return ""


def _expand_equivalent_helper_replacements(
    source: str,
    scoped_source: str,
    *,
    old: str,
    new: str,
    operations: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Expand repeated standalone calls only for a proven helper extraction.

    This repair is intentionally narrow. The replacement must be one direct
    ``self.<helper>(...)`` call, that helper must be inserted by the same
    proposal, and its body must consist of exactly the original call. Only
    line-aligned occurrences inside the approved method are expanded.
    """
    helper_name = _direct_self_helper_name(new)
    if not helper_name:
        return []
    helper_statement = _inserted_helper_single_statement(operations, helper_name)
    old_statement = textwrap.dedent(old).strip()
    if not helper_statement or helper_statement != old_statement:
        return []

    positions = []
    for position in _occurrence_positions(scoped_source, old):
        line_start = max(
            scoped_source.rfind("\n", 0, position),
            scoped_source.rfind("\r", 0, position),
        ) + 1
        if not scoped_source[line_start:position].strip():
            positions.append(position)
    if len(positions) < 2:
        return []

    expanded_rows: list[dict[str, str]] = []
    for position in positions:
        expanded_old = _unique_context_for_occurrence(
            source, scoped_source, old, position
        )
        if not expanded_old:
            return []
        relative = expanded_old.find(old)
        if relative < 0:
            return []
        expanded_new = (
            expanded_old[:relative] + new + expanded_old[relative + len(old):]
        )
        expanded_rows.append({"op": "replace", "old": expanded_old, "new": expanded_new})
    return expanded_rows

def repair_ambiguous_replace_anchors(
    payload: dict[str, Any],
    *,
    project_root: Path,
    instruction: str,
) -> dict[str, Any]:
    """Safely expand ambiguous replace anchors inside an explicit symbol.

    Replace operations and direction-safe insert anchors are changed. Missing
    anchors and operations outside the explicitly named Class.method scope
    remain untouched and are rejected by normal EditManager validation.
    """

    requested = _requested_symbol(instruction)
    if requested is None:
        return payload

    class_name, method_name = requested
    repaired = copy.deepcopy(payload)

    files = repaired.get("files")
    if not isinstance(files, list):
        return repaired

    root = Path(project_root).resolve(strict=False)

    for file_row in files:
        if not isinstance(file_row, dict):
            continue

        raw_path = str(file_row.get("path", "")).strip()
        if not raw_path:
            continue

        candidate = (root / raw_path).resolve(strict=False)

        try:
            candidate.relative_to(root)
        except ValueError:
            continue

        if not candidate.is_file():
            continue

        try:
            source = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue

        scoped_source = _symbol_source(
            source,
            class_name=class_name,
            method_name=method_name,
        )

        operations = file_row.get("operations")
        if not isinstance(operations, list):
            continue

        operation_index = 0
        while operation_index < len(operations):
            operation = operations[operation_index]
            if not isinstance(operation, dict):
                operation_index += 1
                continue
            operation_name = str(operation.get("op", "")).strip().casefold()

            if operation_name == "replace":
                old = operation.get("old")
                new = operation.get("new")

                if not isinstance(old, str) or not isinstance(new, str):
                    operation_index += 1
                    continue

                operation_scope = scoped_source
                if not operation_scope:
                    operation_scope = _unique_class_method_source_for_anchor(
                        source,
                        class_name=class_name,
                        anchor=old,
                        replacement=new,
                    )
                if not operation_scope:
                    operation_index += 1
                    continue

                expanded = _expand_unique_replace(
                    source,
                    operation_scope,
                    old,
                    new,
                )
                if expanded is not None:
                    operation["old"], operation["new"] = expanded
                    operation_index += 1
                    continue
                equivalent_rows = _expand_equivalent_helper_replacements(
                    source,
                    operation_scope,
                    old=old,
                    new=new,
                    operations=[row for row in operations if isinstance(row, dict)],
                )
                if equivalent_rows:
                    operations[operation_index:operation_index + 1] = equivalent_rows
                    operation_index += len(equivalent_rows)
                    continue
                operation_index += 1
                continue

            if operation_name == "delete":
                old = operation.get("old")
                if not isinstance(old, str):
                    operation_index += 1
                    continue

                # A delete anchor cannot simply be expanded: EditManager would
                # delete the added context as well.  Re-express the operation
                # as a replace that preserves that context and removes only the
                # model-requested fragment.  This is safe only when the fragment
                # occurs exactly once inside the explicitly requested symbol.
                operation_scope = scoped_source
                if not operation_scope:
                    operation_scope = _unique_class_method_source_for_anchor(
                        source,
                        class_name=class_name,
                        anchor=old,
                    )
                if not operation_scope:
                    continue

                expanded = _expand_unique_replace(
                    source,
                    operation_scope,
                    old,
                    "",
                )
                if expanded is not None:
                    operation.clear()
                    operation.update(
                        {
                            "op": "replace",
                            "old": expanded[0],
                            "new": expanded[1],
                        }
                    )
                operation_index += 1
                continue

            if operation_name not in {"insert_before", "insert_after"}:
                operation_index += 1
                continue

            anchor = operation.get("anchor")
            if not isinstance(anchor, str):
                operation_index += 1
                continue

            expanded_anchor = _expand_unique_insert_anchor(
                source,
                scoped_source,
                anchor,
                insert_before=operation_name == "insert_before",
            )
            if expanded_anchor:
                operation["anchor"] = expanded_anchor
            operation_index += 1

    return repaired


def reorder_insertions_after_exact_edits(
    payload: dict[str, Any],
    *,
    project_root: Path,
) -> dict[str, Any]:
    """Move insert operations behind exact edits only when proven independent.

    Small code models sometimes insert an extracted helper first.  The inserted
    helper can contain the short anchor used by the following replace/delete,
    turning a source-unique anchor into an ambiguous one during sequential
    application.  Reordering is allowed only when every exact edit is unique in
    the original source, every insertion anchor remains unique after those
    edits, and the reordered sequence can be simulated without error.
    """
    repaired = copy.deepcopy(payload)
    files = repaired.get("files")
    if not isinstance(files, list):
        return repaired

    root = Path(project_root).resolve(strict=False)
    for file_row in files:
        if not isinstance(file_row, dict):
            continue
        operations = file_row.get("operations")
        if not isinstance(operations, list) or len(operations) < 2:
            continue

        insertions = [
            row for row in operations
            if isinstance(row, dict)
            and str(row.get("op", "")).strip().casefold()
            in {"insert_before", "insert_after"}
        ]
        exact_edits = [
            row for row in operations
            if isinstance(row, dict)
            and str(row.get("op", "replace")).strip().casefold()
            in {"replace", "replace_exact", "delete"}
        ]
        if not insertions or not exact_edits:
            continue
        reordered = exact_edits + insertions
        if reordered == operations:
            continue
        # Do not reinterpret payloads containing unknown/mixed operation rows.
        if len(reordered) != len(operations):
            continue

        raw_path = str(file_row.get("path", "")).strip()
        candidate = (root / raw_path).resolve(strict=False)
        try:
            candidate.relative_to(root)
            source = candidate.read_text(encoding="utf-8")
        except (ValueError, OSError, UnicodeError):
            continue

        working = source
        safe = True
        for operation in reordered:
            kind = str(operation.get("op", "replace")).strip().casefold()
            anchor_field = "anchor" if kind.startswith("insert_") else "old"
            anchor = operation.get(anchor_field)
            if not isinstance(anchor, str) or not anchor or working.count(anchor) != 1:
                safe = False
                break
            if kind in {"replace", "replace_exact"}:
                rendered = operation.get("new")
            elif kind == "delete":
                rendered = ""
            else:
                content = operation.get("content")
                if not isinstance(content, str) or not content:
                    safe = False
                    break
                rendered = content + anchor if kind == "insert_before" else anchor + content
            if not isinstance(rendered, str):
                safe = False
                break
            updated = working.replace(anchor, rendered, 1)
            if updated == working:
                safe = False
                break
            working = updated

        if safe:
            file_row["operations"] = reordered

    return repaired


def _occurrence_positions(text: str, fragment: str) -> tuple[int, ...]:
    if not fragment:
        return ()

    positions: list[int] = []
    start = 0

    while True:
        position = text.find(fragment, start)
        if position < 0:
            break
        positions.append(position)
        start = position + max(1, len(fragment))

    return tuple(positions)


def _unique_context_for_occurrence(
    source: str,
    scoped_source: str,
    fragment: str,
    position: int,
) -> str:
    before = scoped_source[:position]
    after = scoped_source[position + len(fragment):]

    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)

    for radius in range(1, 17):
        prefix = "".join(before_lines[-radius:])
        suffix = "".join(after_lines[:radius])
        candidate = prefix + fragment + suffix

        if source.count(candidate) == 1:
            return candidate

    return ""


def build_ambiguous_anchor_guidance(
    payload: dict[str, Any],
    *,
    project_root: Path,
    instruction: str,
    limit: int = 6,
) -> str:
    """Describe exact unique candidates for ambiguous replace anchors.

    This function never chooses or changes an operation. It only returns
    source-derived alternatives so the model can select the intended block.
    """

    requested = _requested_symbol(instruction)
    if requested is None:
        return ""

    class_name, method_name = requested
    root = Path(project_root).resolve(strict=False)

    try:
        maximum = max(1, min(int(limit), 12))
    except (TypeError, ValueError, OverflowError):
        maximum = 6

    rows: list[str] = []
    files = payload.get("files")

    if not isinstance(files, list):
        return ""

    for file_row in files:
        if not isinstance(file_row, dict):
            continue

        raw_path = str(file_row.get("path", "")).strip()
        if not raw_path:
            continue

        candidate = (root / raw_path).resolve(strict=False)

        try:
            candidate.relative_to(root)
        except ValueError:
            continue

        if not candidate.is_file():
            continue

        try:
            source = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue

        scoped_source = _symbol_source(
            source,
            class_name=class_name,
            method_name=method_name,
        )
        if not scoped_source:
            continue

        operations = file_row.get("operations")
        if not isinstance(operations, list):
            continue

        for operation_index, operation in enumerate(operations, start=1):
            if not isinstance(operation, dict):
                continue
            operation_kind = str(operation.get("op", "")).strip().casefold()
            if operation_kind == "replace":
                old = operation.get("old")
            elif operation_kind == "replace_method_block":
                old = operation.get("block_test")
            else:
                continue

            if not isinstance(old, str) or not old:
                continue

            positions = _occurrence_positions(scoped_source, old)
            if len(positions) <= 1:
                continue

            candidates: list[str] = []

            for position in positions:
                expanded = _unique_context_for_occurrence(
                    source,
                    scoped_source,
                    old,
                    position,
                )
                if expanded and expanded not in candidates:
                    candidates.append(expanded)

                if len(candidates) >= maximum:
                    break

            if not candidates:
                continue

            rows.extend(
                (
                    "",
                    f"AMBIGUOUS ANCHOR GUIDANCE: {raw_path} operation {operation_index}",
                    (
                        f"The same old text occurs {len(positions)} times "
                        f"({len(positions)} kez bulundu). "
                        + (
                            "The replace_method_block block_test is structurally ambiguous. "
                            "Do not reuse that generic block_test. Convert this operation to "
                            "a normal replace operation and use exactly one intended source "
                            "candidate below as the old field."
                            if operation_kind == "replace_method_block"
                            else "Use exactly one intended candidate block below as the old field."
                        )
                    ),
                )
            )

            for number, expanded in enumerate(candidates, start=1):
                rows.append(f"\nCANDIDATE {number}:\nADAY {number}:\n{expanded}")

    if not rows:
        return ""

    return "\n".join(rows).strip()


def merge_duplicate_operation_rows(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Merge repeated operation-only rows that target the same file.

    Full-content records and mixed content/operations records are left intact
    so EditManager can reject unsafe or ambiguous payloads normally.
    """

    repaired = copy.deepcopy(payload)
    files = repaired.get("files")

    if not isinstance(files, list):
        return repaired

    merged: list[Any] = []
    operation_rows: dict[str, dict[str, Any]] = {}

    for row in files:
        if not isinstance(row, dict):
            merged.append(row)
            continue

        raw_path = str(row.get("path", "")).strip().replace("\\", "/")
        while raw_path.startswith("./"):
            raw_path = raw_path[2:]

        operations = row.get("operations")
        content = row.get("content")

        if (
            not raw_path
            or not isinstance(operations, list)
            or isinstance(content, str)
        ):
            merged.append(row)
            continue

        key = raw_path.casefold()
        existing = operation_rows.get(key)

        if existing is None:
            copied = copy.deepcopy(row)
            copied["path"] = raw_path
            operation_rows[key] = copied
            merged.append(copied)
            continue

        existing_operations = existing.get("operations")
        if not isinstance(existing_operations, list):
            merged.append(row)
            continue

        existing_operations.extend(copy.deepcopy(operations))

        old_reason = str(existing.get("reason", "")).strip()
        new_reason = str(row.get("reason", "")).strip()

        reasons = [
            value
            for value in (old_reason, new_reason)
            if value
        ]
        existing["reason"] = " | ".join(dict.fromkeys(reasons))

    repaired["files"] = merged
    return repaired


def remove_redundant_noop_replaces(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop literal ``old == new`` rows when real operations remain.

    Removing such a row cannot alter the rendered file. If a file contains
    only no-ops, preserve them so the normal validator still rejects the
    proposal instead of silently accepting an empty change.
    """

    repaired = copy.deepcopy(payload)
    files = repaired.get("files")
    if not isinstance(files, list):
        return repaired

    for file_row in files:
        if not isinstance(file_row, dict):
            continue
        operations = file_row.get("operations")
        if not isinstance(operations, list) or len(operations) < 2:
            continue

        retained: list[Any] = []
        removed = False
        for operation in operations:
            is_literal_noop = (
                isinstance(operation, dict)
                and str(operation.get("op", "replace")).strip().casefold()
                in {"replace", "replace_exact"}
                and isinstance(operation.get("old"), str)
                and operation.get("old") == operation.get("new")
            )
            if is_literal_noop:
                removed = True
                continue
            retained.append(operation)

        if removed and retained:
            file_row["operations"] = retained

    return repaired


def _normalise_anchor_lines(value: str) -> tuple[str, ...]:
    """Normalize indentation without changing token or line ordering."""
    rows = str(value or "").replace("\r\n", "\n").replace("\r", "\n").splitlines()

    while rows and not rows[0].strip():
        rows.pop(0)
    while rows and not rows[-1].strip():
        rows.pop()

    return tuple(" ".join(row.strip().split()) for row in rows)


def _unique_whitespace_match(
    scoped_source: str,
    requested: str,
) -> str:
    requested_rows = _normalise_anchor_lines(requested)

    if not requested_rows:
        return ""

    source_lines = scoped_source.splitlines(keepends=True)
    window_size = len(requested_rows)
    matched_window = ""
    match_count = 0

    for index in range(0, len(source_lines) - window_size + 1):
        window = "".join(source_lines[index:index + window_size])

        if _normalise_anchor_lines(window) != requested_rows:
            continue

        match_count += 1

        if match_count > 1:
            return ""

        matched_window = window

    return matched_window if match_count == 1 else ""


def repair_unique_whitespace_anchors(
    payload: dict[str, Any],
    *,
    project_root: Path,
    instruction: str,
) -> dict[str, Any]:
    """Replace whitespace-variant anchors with one exact source fragment.

    The repair is applied only inside an explicitly requested Class.method.
    Zero or multiple normalized matches are left untouched for EditManager to
    reject normally.
    """

    requested_symbol = _requested_symbol(instruction)
    if requested_symbol is None:
        return payload

    class_name, method_name = requested_symbol
    root = Path(project_root).resolve(strict=False)
    repaired = copy.deepcopy(payload)
    files = repaired.get("files")

    if not isinstance(files, list):
        return repaired

    for file_row in files:
        if not isinstance(file_row, dict):
            continue

        raw_path = str(file_row.get("path", "")).strip().replace("\\", "/")
        if not raw_path:
            continue

        candidate = (root / raw_path).resolve(strict=False)

        try:
            candidate.relative_to(root)
        except ValueError:
            continue

        if not candidate.is_file():
            continue

        try:
            source = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue

        scoped_source = _symbol_source(
            source,
            class_name=class_name,
            method_name=method_name,
        )
        if not scoped_source:
            continue

        operations = file_row.get("operations")
        if not isinstance(operations, list):
            continue

        for operation in operations:
            if not isinstance(operation, dict):
                continue

            operation_name = str(
                operation.get("op", "")
            ).strip().casefold()

            anchor_field = (
                "anchor"
                if operation_name in {"insert_before", "insert_after"}
                else "old"
                if operation_name in {"replace", "delete"}
                else ""
            )

            if not anchor_field:
                continue

            requested_anchor = operation.get(anchor_field)
            if not isinstance(requested_anchor, str) or not requested_anchor:
                continue

            # Exact matches are already valid or handled by ambiguity repair.
            if source.count(requested_anchor) != 0:
                continue

            exact_source_anchor = _unique_whitespace_match(
                scoped_source,
                requested_anchor,
            )
            if not exact_source_anchor:
                continue

            operation[anchor_field] = exact_source_anchor

    return repaired


def _normalised_similarity_text(value: str) -> str:
    rows = _normalise_anchor_lines(value)
    return "\n".join(rows)


def _closest_unique_source_window(
    scoped_source: str,
    requested: str,
) -> tuple[str, float]:
    """Find one strong source excerpt even when line counts differ.

    Overlapping candidates around the same source block are treated as one
    region. A similarly strong candidate from another region makes the result
    ambiguous and therefore unusable as retry guidance.
    """

    requested_rows = _normalise_anchor_lines(requested)
    if not requested_rows:
        return "", 0.0

    source_lines = scoped_source.splitlines(keepends=True)
    if not source_lines:
        return "", 0.0

    requested_text = "\n".join(requested_rows)
    requested_size = len(requested_rows)

    size_delta = max(6, requested_size // 2)
    minimum_size = max(1, requested_size - size_delta)
    maximum_size = min(
        len(source_lines),
        requested_size + size_delta,
    )

    scored: list[tuple[float, int, int, str]] = []

    for window_size in range(minimum_size, maximum_size + 1):
        for index in range(
            0,
            len(source_lines) - window_size + 1,
        ):
            end_index = index + window_size
            window = "".join(source_lines[index:end_index])
            candidate_text = _normalised_similarity_text(window)

            similarity = difflib.SequenceMatcher(
                None,
                requested_text,
                candidate_text,
            ).ratio()

            length_ratio = (
                min(requested_size, window_size)
                / max(requested_size, window_size)
            )

            # Prefer textually similar windows whose size is also reasonably
            # close to the rejected anchor, without requiring equal lengths.
            score = (similarity * 0.90) + (length_ratio * 0.10)

            scored.append(
                (
                    score,
                    index,
                    end_index,
                    window,
                )
            )

    if not scored:
        return "", 0.0

    scored.sort(key=lambda item: item[0], reverse=True)

    best_score, best_start, best_end, best_window = scored[0]

    # This is guidance, not an automatic edit, but still require a meaningful
    # structural resemblance before exposing a source block to the next retry.
    if best_score < 0.62:
        return "", best_score

    for challenger_score, challenger_start, challenger_end, _ in scored[1:]:
        overlaps_best = not (
            challenger_end <= best_start
            or challenger_start >= best_end
        )

        if overlaps_best:
            continue

        # A similarly strong candidate in another source region means the
        # proposed anchor cannot be mapped safely to one exact block.
        if challenger_score >= best_score - 0.035:
            return "", best_score

        # Remaining candidates are sorted lower and cannot become ambiguous.
        break

    return best_window, best_score

def build_missing_anchor_guidance(
    payload: dict[str, Any],
    *,
    project_root: Path,
    instruction: str,
) -> str:
    """Return exact source candidates for zero-match operations.

    This function only provides retry guidance. It never changes the proposal.
    """

    requested_symbol = _requested_symbol(instruction)
    if requested_symbol is None:
        return ""

    class_name, method_name = requested_symbol
    root = Path(project_root).resolve(strict=False)
    rows: list[str] = []

    files = payload.get("files")
    if not isinstance(files, list):
        return ""

    for file_row in files:
        if not isinstance(file_row, dict):
            continue

        raw_path = str(file_row.get("path", "")).strip().replace("\\", "/")
        if not raw_path:
            continue

        candidate = (root / raw_path).resolve(strict=False)

        try:
            candidate.relative_to(root)
        except ValueError:
            continue

        if not candidate.is_file():
            continue

        try:
            source = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue

        scoped_source = _symbol_source(
            source,
            class_name=class_name,
            method_name=method_name,
        )
        if not scoped_source:
            continue

        operations = file_row.get("operations")
        if not isinstance(operations, list):
            continue

        for operation_index, operation in enumerate(operations, start=1):
            if not isinstance(operation, dict):
                continue

            operation_name = str(
                operation.get("op", "")
            ).strip().casefold()

            if operation_name == "insert_class_method":
                proposed_class = str(operation.get("class_name", "")).strip()
                if proposed_class and proposed_class != class_name:
                    rows.extend((
                        "",
                        (
                            f"INVALID STRUCTURAL CLASS TARGET: {raw_path} "
                            f"operation {operation_index}"
                        ),
                        (
                            f"Approved class_name is {class_name}. "
                            f"The proposed class_name {proposed_class!r} is invalid."
                        ),
                        (
                            f"The approved runtime symbol {class_name}.{method_name} "
                            f"must be split as class_name={class_name!r} and "
                            f"method_name={method_name!r}. Nested runtime segments "
                            "must not be appended to class_name."
                        ),
                        (
                            "For insert_class_method, use only the owning class name. "
                            f"Set class_name to {class_name!r}. Do not repeat "
                            f"class_name={proposed_class!r}."
                        ),
                        f"\nAPPROVED METHOD SOURCE:\n{scoped_source}",
                    ))
                continue

            if operation_name == "replace_method_block":
                raw_block_test = str(operation.get("block_test", "") or "").strip()
                requested_anchor = _normalize_if_test_selector(raw_block_test)
                if not requested_anchor:
                    rows.extend(("", f"INVALID STRUCTURAL BLOCK: {raw_path} operation {operation_index}", "Use an exact existing if condition or a unique source-backed replace anchor.", f"\nAPPROVED METHOD SOURCE:\n{scoped_source}"))
                    continue

                try:
                    scoped_tree = ast.parse(textwrap.dedent(scoped_source))
                except SyntaxError:
                    continue

                if_tests = [
                    ast.unparse(node.test)
                    for node in ast.walk(scoped_tree)
                    if isinstance(node, ast.If)
                ]
                wanted = _expression_fingerprint(requested_anchor)
                matching_tests = [
                    test
                    for test in if_tests
                    if _expression_fingerprint(test) == wanted
                ]
                if matching_tests:
                    continue

                rows.extend((
                    "",
                    (
                        f"MISSING STRUCTURAL BLOCK: {raw_path} "
                        f"operation {operation_index}"
                    ),
                    (
                        f"Approved editable scope is {class_name}.{method_name}. "
                        f"The requested if condition does not exist in that method: "
                        f"{requested_anchor}"
                    ),
                    (
                        "Do not repeat replace_method_block with the same condition. "
                        "Use only an exact statement that exists in the source, or "
                        "return no edit when the evidence does not justify one."
                    ),
                    (
                        "Existing if conditions in the approved method: "
                        + ("; ".join(if_tests) if if_tests else "(none)")
                    ),
                    f"\nAPPROVED METHOD SOURCE:\n{scoped_source}",
                ))
                continue

            field = (
                "anchor"
                if operation_name in {"insert_before", "insert_after"}
                else "old"
                if operation_name in {"replace", "delete"}
                else ""
            )

            if not field:
                continue

            requested_anchor = operation.get(field)
            if not isinstance(requested_anchor, str) or not requested_anchor:
                continue

            if source.count(requested_anchor) != 0:
                continue

            closest, score = _closest_unique_source_window(
                scoped_source,
                requested_anchor,
            )
            if not closest:
                rows.extend(("", f"MISSING ANCHOR GUIDANCE: {raw_path} operation {operation_index}", "Choose a short exact method-local block from the approved live source.", f"\nAPPROVED METHOD SOURCE:\n{scoped_source}"))
                continue

            rows.extend(
                (
                    "",
                    (
                        f"EKS?K ANCHOR REHBER?: {raw_path} "
                        f"i?lem {operation_index}"
                    ),
                    (
                        f"Model anchor? kaynakta bulunmad?. En yak?n benzersiz "
                        f"ger?ek kaynak blo?u (benzerlik %{int(score * 100)}):"
                    ),
                    "Bu blo?u birebir kullan veya i?lemi yeniden tasarla.",
                    f"\nGER?EK KAYNAK BLO?U:\n{closest}",
                    f"\nAPPROVED METHOD SOURCE:\n{scoped_source}",
                )
            )

    return "\n".join(rows).strip()


def qualify_inserted_private_helper_calls(
    payload: dict[str, Any],
    *,
    instruction: str,
) -> dict[str, Any]:
    """Qualify bare calls to newly inserted private sibling methods.

    A code model may correctly add ``VoiceService._helper`` but use
    ``_helper(...)`` in the approved method replacement. Inside an instance
    method that call must be ``self._helper(...)``. Only helpers introduced by
    ``insert_class_method`` in the explicitly requested class are eligible.
    """
    requested = _requested_symbol(instruction)
    if requested is None:
        return payload

    requested_class, _requested_method = requested
    repaired = copy.deepcopy(payload)
    files = repaired.get("files")

    if not isinstance(files, list):
        return repaired

    for file_row in files:
        if not isinstance(file_row, dict):
            continue

        operations = file_row.get("operations")
        if not isinstance(operations, list):
            continue

        helper_names: set[str] = set()

        for operation in operations:
            if not isinstance(operation, dict):
                continue
            if str(operation.get("op", "")).strip().casefold() != (
                "insert_class_method"
            ):
                continue
            if str(operation.get("class_name", "")).strip() != requested_class:
                continue

            content = operation.get("content")
            if not isinstance(content, str) or not content.strip():
                continue

            try:
                parsed = ast.parse(textwrap.dedent(content))
            except SyntaxError:
                continue

            if (
                len(parsed.body) == 1
                and isinstance(
                    parsed.body[0],
                    (ast.FunctionDef, ast.AsyncFunctionDef),
                )
                and parsed.body[0].name.startswith("_")
            ):
                helper_names.add(parsed.body[0].name)

        if not helper_names:
            continue

        for operation in operations:
            if not isinstance(operation, dict):
                continue
            if str(operation.get("op", "")).strip().casefold() not in {
                "replace",
                "replace_exact",
            }:
                continue

            new_text = operation.get("new")
            if not isinstance(new_text, str) or not new_text.strip():
                continue

            try:
                tree = ast.parse(new_text)
            except SyntaxError:
                try:
                    expression = ast.parse(new_text, mode="eval")
                except SyntaxError:
                    continue
                tree = expression

            changed = False

            class QualifyCalls(ast.NodeTransformer):
                def visit_Call(self, node: ast.Call) -> ast.AST:
                    nonlocal changed
                    self.generic_visit(node)

                    if (
                        isinstance(node.func, ast.Name)
                        and node.func.id in helper_names
                    ):
                        node.func = ast.Attribute(
                            value=ast.Name(id="self", ctx=ast.Load()),
                            attr=node.func.id,
                            ctx=ast.Load(),
                        )
                        changed = True

                    return node

            tree = QualifyCalls().visit(tree)
            ast.fix_missing_locations(tree)

            if not changed:
                continue

            if isinstance(tree, ast.Expression):
                operation["new"] = ast.unparse(tree.body)
            else:
                operation["new"] = ast.unparse(tree)

    return repaired


def ground_requested_docstring_replace_anchors(
    payload: dict[str, Any],
    *,
    project_root: Path,
    instruction: str,
) -> dict[str, Any]:
    """Ground a docstring-only replace anchor from the live AST.

    The model remains responsible for the replacement docstring. Jarvis only
    replaces a missing model-generated ``old`` value when all of the following
    are proven from the request and live source:

    * the request explicitly says docstring;
    * the operation targets an existing Python file;
    * the requested function/method name is explicit in backticks or as a
      dotted symbol;
    * the live target has exactly one docstring statement;
    * the replacement is itself only a string-literal statement.

    This never guesses executable code and never broadens file scope.
    """
    raw_instruction = str(instruction or "")
    if "docstring" not in raw_instruction.casefold():
        return copy.deepcopy(payload)

    repaired = copy.deepcopy(payload)
    files = repaired.get("files")
    if not isinstance(files, list):
        return repaired

    explicit_names = re.findall(
        r"`([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)`",
        raw_instruction,
    )
    dotted = [
        match.group(0)
        for match in _DOTTED_SYMBOL_PATTERN.finditer(raw_instruction)
        if not match.group(0).casefold().endswith(".py")
    ]

    # Runtime code-model prompts do not always preserve the user's exact
    # backtick syntax.  The model payload itself frequently carries the target
    # symbol in summary/reason fields, so use those fields only as additional
    # symbol-name evidence.  The final anchor still comes exclusively from the
    # live AST and must be unique.
    payload_evidence: list[str] = []
    summary = repaired.get("summary")
    if isinstance(summary, str):
        payload_evidence.append(summary)
    for file_row in repaired.get("files", ()):
        if not isinstance(file_row, dict):
            continue
        reason = file_row.get("reason")
        if isinstance(reason, str):
            payload_evidence.append(reason)

    payload_names: list[str] = []
    for evidence in payload_evidence:
        payload_names.extend(
            re.findall(r"\b(_[A-Za-z0-9_]+|[A-Za-z][A-Za-z0-9_]{2,})\b", evidence)
        )

    # Payload evidence is closest to the actual proposed edit and must win
    # over unrelated symbols present elsewhere in the large code-model prompt.
    requested_names = list(dict.fromkeys([*payload_names, *explicit_names, *dotted]))
    if not requested_names:
        return repaired

    root = Path(project_root).resolve(strict=False)

    def find_target(tree: ast.Module, requested: str):
        parts = requested.split(".")
        if len(parts) == 2:
            class_name, function_name = parts
            matches = []
            for owner in tree.body:
                if isinstance(owner, ast.ClassDef) and owner.name == class_name:
                    matches.extend(
                        child for child in owner.body
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and child.name == function_name
                    )
            return matches
        function_name = parts[-1]
        matches = [
            node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == function_name
        ]
        # Also allow a unique direct class method when the user supplied only
        # the method name. Uniqueness is the proof; no class is guessed.
        for owner in tree.body:
            if not isinstance(owner, ast.ClassDef):
                continue
            matches.extend(
                child for child in owner.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                and child.name == function_name
            )
        return matches

    for file_row in files:
        if not isinstance(file_row, dict):
            continue
        raw_path = str(file_row.get("path", "")).strip().replace("\\", "/")
        if not raw_path.endswith(".py"):
            continue
        candidate = (root / raw_path).resolve(strict=False)
        try:
            candidate.relative_to(root)
            source = candidate.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=raw_path)
        except (ValueError, OSError, UnicodeError, SyntaxError):
            continue

        target = None
        for requested in requested_names:
            matches = find_target(tree, requested)
            if len(matches) == 1:
                target = matches[0]
                break
        if target is None or not target.body:
            continue

        first = target.body[0]
        if not (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            continue
        live_docstring = ast.get_source_segment(source, first)
        if not live_docstring or source.count(live_docstring) != 1:
            continue

        operations = file_row.get("operations")
        if not isinstance(operations, list):
            continue
        for operation in operations:
            if not isinstance(operation, dict):
                continue
            if str(operation.get("op", "")).strip().casefold() != "replace":
                continue
            old = operation.get("old")
            new = operation.get("new")
            if not isinstance(old, str) or not isinstance(new, str):
                continue
            if old and old in source:
                continue

            # Prove that the model replacement is a docstring statement only.
            try:
                parsed_new = ast.parse(textwrap.dedent(new).strip())
            except SyntaxError:
                continue
            if (
                len(parsed_new.body) != 1
                or not isinstance(parsed_new.body[0], ast.Expr)
                or not isinstance(parsed_new.body[0].value, ast.Constant)
                or not isinstance(parsed_new.body[0].value.value, str)
            ):
                continue

            operation["old"] = live_docstring
            operation["_live_ast_grounded"] = "docstring"
    return repaired
