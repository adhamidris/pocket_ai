from __future__ import annotations

from apps.rag.lexicon.text_utils import PLURAL_BLACKLIST as _SHARED_PLURAL_BLACKLIST


class QueryClassifierPatternMixin:

    # Enumeration indicators - strong signals for ENUMERATE intent
    ENUMERATE_PATTERNS = [
        r'\b(list|show|display|give)\s+(me\s+)?(all|every|each)\b',
        r'\ball\s+(the\s+)?\w+s?\b',
        r'\bevery\s+(single\s+)?\w+\b',
        r'\bwhat\s+(are\s+)?(all|the)\s+\w+s\b',
        r'\bentire\s+(list|set|collection)\b',
        r'\bcomplete\s+(list|overview)\b',
        r'\bfull\s+list\b',
    ]

    # Enumerate keywords - words that signal enumeration intent
    ENUMERATE_KEYWORDS = {
        'all', 'every', 'each', 'entire', 'complete', 'full',
        'whole', 'everything', 'comprehensive',
        'كل', 'جميع', 'كافه', 'كافة', 'الكل',
    }

    # List action verbs - verbs that often precede enumeration
    LIST_VERBS = {
        'list', 'show', 'display', 'enumerate', 'give', 'tell',
        'اعرض', 'عرض', 'هات', 'اعطني', 'اذكر',
    }

    ALL_SCOPE_TOKENS = {
        "all",
        "every",
        "each",
        "entire",
        "full",
        "whole",
        "كل",
        "جميع",
        "كافة",
        "كافه",
        "الكل",
    }

    # Comparison indicators
    COMPARE_PATTERNS = [
        r'\bcompare\b',
        r'\bvs\.?\b',
        r'\bversus\b',
        r'\bdifference\s+between\b',
        r'\bcompared\s+to\b',
        r'\bbetter\s+than\b',
        r'\bworse\s+than\b',
        r'\bقارن\b',
        r'\bمقارنه\b',
        r'\bمقارنة\b',
        r'\bالفرق\s+بين\b',
    ]

    # Aggregation indicators
    AGGREGATE_PATTERNS = [
        r'\btotal\b',
        r'\bsum\b',
        r'\baverage\b',
        r'\bcount\b',
        r'\bhow\s+many\b',
        r'\bminimum\b',
        r'\bmaximum\b',
        r'\bhighest\b',
        r'\blowest\b',
        r'\bاجمالي\b',
        r'\bإجمالي\b',
        r'\bالمجموع\b',
        r'\bمتوسط\b',
        r'\bكم\b',
    ]

    # Specific lookup indicators - signals for SPECIFIC_LOOKUP
    SPECIFIC_PATTERNS = [
        r'\bwhat\s+is\s+the\s+\w+\b',
        r'\bdetails?\s+for\s+(the\s+)?\w+\b',
        r'\binfo(?:rmation)?\s+for\s+(the\s+)?\w+\b',
    ]

    # Domain-agnostic control words used to isolate entity/attribute candidates.
    # These are structural query terms, not business-domain vocab.
    CONTROL_TOKENS = {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
        "how", "in", "is", "it", "its", "me", "of", "on", "or", "show",
        "tell", "that", "the", "their", "them", "these", "those", "this",
        "to", "what", "which", "who", "with", "you", "your", "about", "all",
        "every", "each", "any", "some", "list", "display", "give", "enumerate",
        "compare", "comparison", "versus", "vs", "between", "difference", "differ",
        "total", "sum", "average", "count", "minimum", "maximum", "highest", "lowest",
        "mean", "many", "more", "few", "several", "please",
        "ما", "ماذا", "كيف", "من", "عن", "مع", "في", "على", "الى", "إلى",
        "هذا", "هذه", "ذلك", "تلك", "كل", "جميع", "او", "أو", "و", "ثم",
        "اعرض", "عرض", "قارن", "مقارنة", "اجمالي", "إجمالي", "المجموع",
    }
    ENTITY_BOUNDARY_TOKENS = {
        "and", "or", "with", "without", "their", "its", "this", "that", "these",
        "those", "for", "from", "to", "in", "on", "at", "by", "of", "about",
        "regarding", "where", "when", "which", "who", "what", "how",
        "عن", "مع", "في", "على", "الى", "إلى", "من", "او", "أو", "و", "ثم",
        "التي", "الذي", "ما", "ماذا", "كيف",
    }
    ENTITY_NOISE_PREFIX_TOKENS = {
        "the", "a", "an", "all", "every", "each", "any", "some",
        "available", "current", "latest", "new", "existing", "active",
        "ال", "كل", "جميع", "كافة", "هذا", "هذه", "ذلك", "تلك",
    }
    ENTITY_CAPTURE_ANCHORS = {
        "all", "every", "each", "some", "many", "few", "several", "for", "about", "regarding",
        "كل", "جميع", "كافة", "عن",
    }
    PLURAL_BLACKLIST = _SHARED_PLURAL_BLACKLIST
    COMPARE_TOKENS = {
        "vs",
        "versus",
        "compare",
        "comparison",
        "between",
        "differ",
        "difference",
        "قارن",
        "مقارنة",
        "مقارنه",
        "الفرق",
    }
    AGGREGATE_TOKENS = {
        "total",
        "sum",
        "average",
        "count",
        "minimum",
        "maximum",
        "mean",
        "اجمالي",
        "إجمالي",
        "المجموع",
        "متوسط",
    }
    SECTION_SEEKING_PATTERNS = [
        r"\bwork\s+experience\b",
        r"\bwork\s+history\b",
        r"\bemployment\s+history\b",
        r"\bjob\s+titles?\b",
        r"\bjob\s+positions?\b",
        r"\bterms?\s+and\s+conditions\b",
    ]
    SECTION_COVERAGE_HEADWORDS = {
        "title",
        "titles",
        "role",
        "roles",
        "position",
        "positions",
        "responsibility",
        "responsibilities",
        "duty",
        "duties",
        "term",
        "terms",
        "clause",
        "clauses",
        "section",
        "sections",
        "benefit",
        "benefits",
        "fee",
        "fees",
        "charge",
        "charges",
        "service",
        "services",
        "product",
        "products",
        "job",
        "jobs",
        "experience",
        "experiences",
        "history",
        "employment",
        "skill",
        "skills",
        "qualification",
        "qualifications",
        "requirement",
        "requirements",
        "location",
        "locations",
    }
