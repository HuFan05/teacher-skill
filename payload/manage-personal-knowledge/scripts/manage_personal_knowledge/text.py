from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unicodedata


_ASCII_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_CJK_RUN_RE = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]+"
)
SEARCH_FORMAT_VERSION = "mpk-search/v1"

# Chinese CS/AI term bridge.  It mirrors the pdf-paper-search query
# canonicalizer, which searches this index directly: a term replaced on one
# side but kept as CJK text on the other would never match.  Keys are
# casefolded because the bridge runs after NFKC and casefolding; longer terms
# are replaced first.
_CHINESE_TERM_BRIDGE = tuple(
    sorted(
        (
            ("随机梯度下降", "stochastic gradient descent"),
            ("卷积神经网络", "convolutional neural network"),
            ("循环神经网络", "recurrent neural network"),
            ("交叉注意力", "cross-attention"),
            ("多头注意力", "multi-head attention"),
            ("时间复杂度", "time complexity"),
            ("空间复杂度", "space complexity"),
            ("多项式时间", "polynomial time"),
            ("自注意力", "self-attention"),
            ("反向传播", "backpropagation"),
            ("梯度消失", "vanishing gradient"),
            ("梯度爆炸", "exploding gradient"),
            ("梯度下降", "gradient descent"),
            ("强化学习", "reinforcement learning"),
            ("监督学习", "supervised learning"),
            ("近似算法", "approximation algorithm"),
            ("np完全", "np-complete"),
            ("最坏情况", "worst case"),
            ("平均情况", "average case"),
            ("损失函数", "loss function"),
            ("目标函数", "objective function"),
            ("残差连接", "residual connection"),
            ("批归一化", "batch normalization"),
            ("层归一化", "layer normalization"),
            ("基于模型", "model-based"),
            ("策略梯度", "policy gradient"),
            ("实验结果", "experimental results"),
            ("注意力", "attention"),
            ("自监督", "self-supervised"),
            ("无监督", "unsupervised"),
            ("近似比", "approximation ratio"),
            ("np难", "np-hard"),
            ("随机化", "randomized"),
            ("确定性", "deterministic"),
            ("交叉熵", "cross-entropy"),
            ("预训练", "pretraining"),
            ("零样本", "zero-shot"),
            ("少样本", "few-shot"),
            ("同策略", "on-policy"),
            ("异策略", "off-policy"),
            ("无模型", "model-free"),
            ("遗憾界", "regret bound"),
            ("编码器", "encoder"),
            ("解码器", "decoder"),
            ("准确率", "accuracy"),
            ("伪代码", "pseudocode"),
            ("上界", "upper bound"),
            ("下界", "lower bound"),
            ("均摊", "amortized"),
            ("微调", "fine-tuning"),
            ("非凸", "nonconvex"),
            ("定理", "theorem"),
            ("引理", "lemma"),
            ("命题", "proposition"),
            ("推论", "corollary"),
            ("定义", "definition"),
            ("算法", "algorithm"),
            ("证明", "prove"),
            ("出处", "source"),
        ),
        key=lambda item: -len(item[0]),
    )
)

# Notation-level folding shared by indexed PDF text and library queries.  The
# input is already NFKC-normalized and casefolded, so uppercase Greek letters
# and compatibility variants (for example the micro sign or the theta symbol)
# arrive here as their lowercase base letters.
_NOTATION_REPLACEMENTS = (
    ("!=", " neq "),
    ("α", " alpha "),
    ("β", " beta "),
    ("γ", " gamma "),
    ("δ", " delta "),
    ("ε", " epsilon "),
    ("ζ", " zeta "),
    ("η", " eta "),
    ("θ", " theta "),
    ("κ", " kappa "),
    ("λ", " lambda "),
    ("μ", " mu "),
    ("ν", " nu "),
    ("ξ", " xi "),
    ("π", " pi "),
    ("ρ", " rho "),
    ("σ", " sigma "),
    ("τ", " tau "),
    ("φ", " phi "),
    ("χ", " chi "),
    ("ψ", " psi "),
    ("ω", " omega "),
    # Greek letters damaged by a legacy-codepage decode of UTF-8 text.
    ("蠁", " phi "),
    ("蠒", " phi "),
    ("渭", " mu "),
    ("蟽", " sigma "),
    ("蟺", " pi "),
    ("∇", " nabla "),
    ("∂", " partial "),
    ("∞", " infinity "),
    ("∑", " sum "),
    ("∏", " product "),
    ("√", " sqrt "),
    ("≤", " le "),
    ("≥", " ge "),
    ("≠", " neq "),
    ("→", " to "),
    ("←", " gets "),
    ("∈", " in "),
    ("∉", " notin "),
)

# TeX commands whose ASCII name differs from the canonical token.  Other
# commands such as ``\alpha`` already tokenize to their name.  A command is
# replaced only at its boundary, so ``\in`` never rewrites ``\infty``.
_TEX_COMMAND_REPLACEMENTS = {
    "varphi": "phi",
    "vartheta": "theta",
    "varepsilon": "epsilon",
    "varsigma": "sigma",
    "varpi": "pi",
    "varrho": "rho",
    "infty": "infinity",
    "prod": "product",
    "frac": "fraction",
    "leq": "le",
    "geq": "ge",
    "ne": "neq",
    "rightarrow": "to",
    "leftarrow": "gets",
}
_TEX_COMMAND_RE = re.compile(
    r"\\(" + "|".join(sorted(_TEX_COMMAND_REPLACEMENTS, key=len, reverse=True)) + r")(?![a-z])"
)
_ARG_OPERATOR_RE = re.compile(r"(?<![a-z0-9])arg\s*(?:\\[,;:! ]\s*)*\\?\s*(max|min)(?![a-z])")


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """Raw text emitted by an extractor, including PDF page form-feeds."""

    text: str
    method: str = "pdftotext"
    warning: str | None = None


class PdfTextExtractionError(RuntimeError):
    pass


def normalize_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def canonicalize_search_text(value: str) -> str:
    """Return the versioned lexical form used by the local FTS index.

    This deliberately performs only notation-level normalization plus the
    shared Chinese term bridge.  Contextual aliases such as ``sgd`` ->
    ``stochastic gradient descent`` belong to ``pdf-paper-search`` and are not
    globally injected into ordinary library text.  The separate ``paper-canonical/v1`` query contract meets this format
    through the tested ASCII anchor contract, not by claiming byte-for-byte
    canonical equivalence.
    """

    normalized = normalize_text(value)
    normalized = _TEX_COMMAND_RE.sub(
        lambda match: f" {_TEX_COMMAND_REPLACEMENTS[match.group(1)]} ", normalized
    )
    for source, replacement in _CHINESE_TERM_BRIDGE:
        normalized = normalized.replace(source, f" {replacement} ")
    for source, replacement in _NOTATION_REPLACEMENTS:
        normalized = normalized.replace(source, replacement)
    # Emit both the joined operator and its parts, so a query written either
    # way (``argmax`` or ``arg max``) finds the other form.
    normalized = _ARG_OPERATOR_RE.sub(r" arg\1 arg \1 ", normalized)
    normalized = normalized.replace("/", " over ")
    decomposed = unicodedata.normalize("NFKD", normalized)
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def split_pdf_pages(raw_text: str) -> list[str]:
    """Split pdftotext output without losing interior blank PDF pages."""

    pages = raw_text.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    if not pages and raw_text:
        return [raw_text]
    return pages


def compact_snippet(value: str, limit: int = 360) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)].rstrip() + "…"


def _cjk_bigrams(run: str) -> list[str]:
    if len(run) < 2:
        return [run]
    return [run[index : index + 2] for index in range(len(run) - 1)]


def search_tokens(value: str, *, query: bool = False) -> list[str]:
    """Return deterministic ASCII tokens plus CJK runs and bigrams.

    Indexed text keeps complete CJK runs for exact labels and also stores
    bigrams for substring retrieval. Query text uses bigrams for runs longer
    than two characters, because a long query run usually occurs inside a
    longer sentence token rather than matching it exactly.
    """

    normalized = canonicalize_search_text(value)
    tokens = _ASCII_TOKEN_RE.findall(normalized)
    for run in _CJK_RUN_RE.findall(normalized):
        if not query or len(run) <= 2:
            tokens.append(run)
        tokens.extend(_cjk_bigrams(run))
    return _dedupe(tokens)


def make_search_text(*values: str) -> str:
    tokens: list[str] = []
    for value in values:
        tokens.extend(search_tokens(value))
    return " ".join(_dedupe(tokens))


def make_match_query(value: str) -> str:
    tokens = search_tokens(value, query=True)
    if not tokens:
        raise ValueError("Search text contains no indexable terms")
    return " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _is_ascii_path(path: Path) -> bool:
    try:
        str(path).encode("ascii")
    except UnicodeEncodeError:
        return False
    return True


def _choose_ascii_temp_root(explicit: Path | None) -> Path:
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit)
    candidates.append(Path(tempfile.gettempdir()) / "manage-personal-knowledge")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(Path(local_app_data) / "manage-personal-knowledge" / "tmp")
    if os.name == "nt":
        system_drive = os.environ.get("SystemDrive", "C:")
        candidates.append(Path(system_drive + "\\Windows\\Temp\\manage-personal-knowledge"))

    for candidate in candidates:
        if not _is_ascii_path(candidate):
            continue
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write-test"
            probe.touch(exist_ok=True)
            probe.unlink()
        except OSError:
            continue
        return candidate
    raise PdfTextExtractionError(
        "No writable ASCII-only temporary directory is available; pass temp_root explicitly"
    )


def find_pdftotext(explicit: str | Path | None = None) -> Path | None:
    """Locate a usable pdftotext executable, preferring bounded known paths."""

    candidates: list[str | Path] = []
    if explicit is not None:
        raw = os.fspath(explicit)
        resolved_command = shutil.which(raw) if not Path(raw).is_file() else raw
        if not resolved_command:
            return None
        path = Path(resolved_command).expanduser().resolve(strict=False)
        return path if path.is_file() else None
    configured = os.environ.get("MPK_PDFTOTEXT")
    if configured:
        candidates.append(configured)
    if os.name == "nt":
        candidates.extend(
            [
                Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
                / "Git"
                / "mingw64"
                / "bin"
                / "pdftotext.exe",
                Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
                / "poppler"
                / "Library"
                / "bin"
                / "pdftotext.exe",
            ]
        )
    path_command = shutil.which("pdftotext")
    if path_command:
        candidates.append(path_command)

    seen: set[str] = set()
    for value in candidates:
        raw = os.fspath(value)
        resolved_command = shutil.which(raw) if not Path(raw).is_file() else raw
        if not resolved_command:
            continue
        path = Path(resolved_command).expanduser().resolve(strict=False)
        key = os.path.normcase(str(path))
        if key in seen:
            continue
        seen.add(key)
        if path.is_file():
            return path
    return None


def extract_pdf_text(
    file_path: Path,
    *,
    pdftotext_exe: str | Path | None = None,
    temp_root: Path | None = None,
    timeout: int = 600,
) -> ExtractionResult:
    """Extract a PDF text layer through an ASCII-only staged input path.

    A hard link is attempted first and a metadata-preserving copy is used when
    links are unavailable. The source PDF is never opened for writing.
    """

    source = Path(file_path)
    if not source.is_file():
        raise FileNotFoundError(source)
    executable_path = find_pdftotext(pdftotext_exe)
    if executable_path is None:
        raise FileNotFoundError("pdftotext was not found on PATH")
    executable = str(executable_path)

    ascii_root = _choose_ascii_temp_root(temp_root)
    process: subprocess.CompletedProcess[str] | None = None
    with tempfile.TemporaryDirectory(prefix="mpk_", dir=ascii_root) as temp_dir:
        staged_pdf = Path(temp_dir) / "input.pdf"
        try:
            os.link(source, staged_pdf)
        except OSError:
            shutil.copy2(source, staged_pdf)
        try:
            process = subprocess.run(
                [executable, "-enc", "UTF-8", "-layout", str(staged_pdf), "-"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise PdfTextExtractionError(
                f"pdftotext timed out after {timeout} seconds: {source}"
            ) from exc

    if process is None:
        raise PdfTextExtractionError(f"pdftotext did not run: {source}")
    stderr = compact_snippet(process.stderr or "", limit=500) or None
    stdout = process.stdout or ""
    if process.returncode != 0:
        raise PdfTextExtractionError(stderr or f"pdftotext exited {process.returncode}")
    warning = stderr if stderr else None
    return ExtractionResult(text=stdout, method="pdftotext", warning=warning)
