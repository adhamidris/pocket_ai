import React from "react";
import { Link } from "react-router-dom";
import {
  CalendarDays,
  ChevronDown,
  ChevronRight,
  Clock,
  EllipsisVertical,
  Filter,
  Inbox,
  Layers3,
  ListFilter,
  Mail,
  MessageCircle,
  MoreHorizontal,
  Search,
  User,
  Users,
  CheckCircle2,
  AlertTriangle,
  Phone,
  FileText,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Separator } from "@/components/ui/separator";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Calendar } from "@/components/ui/calendar";
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Pagination,
  PaginationContent,
  PaginationItem,
  PaginationLink,
  PaginationNext,
  PaginationPrevious,
} from "@/components/ui/pagination";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useEffect } from "react";

type Priority = "low" | "medium" | "high" | "urgent";
type Status = "open" | "resolved";
type Channel = "email" | "chat" | "whatsapp" | "phone";
type Category = "Inquiry" | "Request" | "Complaint";
type CaseType = "billing" | "bug" | "request" | "inquiry" | "complaint" | "feedback";
type CaseItem = {
  id: string;
  customer: { name: string; email: string; avatar?: string };
  title: string;
  snippet: string;
  priority: Priority;
  status: Status;
  channel: Channel;
  assignee?: { name: string; avatar?: string };
  startedAt: string; // ISO
  unread: number;
  category: Category;
  documents?: { id: string; name: string; size: string; type: string }[];
  history?: { id: string; at: string; actor: string; action: string }[];
};

const Sidebar = ({ active = "Cases" as const }) => {
  const items = [
    { label: "Home", to: "/dashboard" },
    { label: "Cases", to: "/dashboard/cases" },
    { label: "Customers", to: "/dashboard/customers" },
    { label: "Agents", to: "/dashboard/agents" },
  ] as const;
  return (
    <aside className="hidden md:flex w-56 shrink-0 border-r border-border/70 bg-card/60 backdrop-blur-sm min-h-screen sticky top-0">
      <div className="flex flex-col w-full p-3 gap-2">
        <div className="px-2 py-3 text-lg font-semibold">Dashboard <span className="text-xs align-top text-muted-foreground">v0.1</span></div>
        <nav className="mt-1 flex-1 space-y-1">
          {items.map((it) => (
            <Link
              key={it.label}
              to={it.to}
              className={`w-full block text-left px-3 py-2 rounded-md text-sm flex items-center justify-between ${
                it.label === active ? "bg-primary/10 text-primary" : "hover:bg-muted/60"
              }`}
            >
              <span>{it.label}</span>
            </Link>
          ))}
        </nav>
        <div className="mt-auto rounded-xl p-3 bg-gradient-to-br from-primary/20 via-primary/10 to-primary/5">
          <div className="text-sm font-semibold">Upgrade to PRO</div>
          <div className="text-xs text-muted-foreground mb-2">Get access to all features</div>
          <Button size="sm" className="w-full bg-gradient-primary text-white hover:opacity-90">
            Get Pro Now!
          </Button>
        </div>
        <div className="flex items-center gap-2 px-2 py-3">
          <div className="h-8 w-8 rounded-full bg-muted" />
          <div>
            <div className="text-sm font-medium">Evano</div>
            <div className="text-xs text-muted-foreground">Project Manager</div>
          </div>
        </div>
      </div>
    </aside>
  );
};

const priorityColor = (p: Priority) => {
  switch (p) {
    case "low":
      return "bg-muted text-muted-foreground";
    case "medium":
      return "bg-blue-500/10 text-blue-600 dark:text-blue-400";
    case "high":
      return "bg-amber-500/10 text-amber-700 dark:text-amber-300";
    case "urgent":
      return "bg-rose-500/10 text-rose-600 dark:text-rose-300";
  }
};

const channelIcon = (ch: Channel) => {
  switch (ch) {
    case "email":
      return Mail;
    case "chat":
      return MessageCircle;
    case "whatsapp":
      return Inbox;
    case "phone":
      return Phone;
  }
};

const formatRelative = (iso: string) => {
  const d = new Date(iso);
  const diff = Date.now() - d.getTime();
  const mins = Math.round(diff / 60000);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  return `${days}d ago`;
};

const deriveCaseType = (item: CaseItem | null): CaseType => {
  if (!item) return "inquiry";
  const text = `${item.title} ${item.snippet}`.toLowerCase();
  if (text.includes("charge") || text.includes("billing")) return "billing";
  if (item.category === "Complaint") return "complaint";
  if (item.category === "Request" || text.includes("request")) return "request";
  if (text.includes("bug") || text.includes("issue") || text.includes("error") || text.includes("trouble") || text.includes("fault")) return "bug";
  if (text.includes("feedback") || text.includes("review")) return "feedback";
  return "inquiry";
};

const AI_DIAGNOSES: Record<CaseType, string[]> = {
  billing: ["Duplicate transaction on the latest invoice.", "Statement total reflects the unintended repeat charge."],
  request: ["CSV export for analytics requested.", "Needed to share reports with stakeholders."],
  bug: ["Integration webhook experienced timeouts.", "Event delivery intermittently failed in the affected window."],
  complaint: ["Service outcome did not meet expectations.", "Dissatisfaction reported with the recent experience."],
  inquiry: ["Clarification requested on a product capability.", "Information needed before proceeding."],
  feedback: ["Constructive feedback shared on product/support.", "Captured for future improvement planning."],
};

const AI_ACTIONS_TAKEN: Record<CaseType, string[]> = {
  billing: ["Collected invoice IDs from user", "Summarized billing history to the customer"],
  request: ["Captured user use-case and frequency"],
  bug: ["Guided user through workaround", "Checked rate limits and recent errors"],
  complaint: ["Analyzed queue wait times"],
  inquiry: ["Provided feature overview and examples"],
  feedback: ["Tagged product team with summary"],
};

const SUGGESTED_ACTIONS: Record<CaseType, string[]> = {
  billing: ["Verify last two invoices", "Issue refund for duplicate charge"],
  request: ["Create product ticket and tag as feature"],
  bug: ["Escalate to the engineering team", "Verify recent errors and logs"],
  complaint: ["Apologize and share SLA timeline"],
  inquiry: ["Share documentation and clarify usage"],
  feedback: [],
};

const CASE_TIMELINE: Record<CaseType, { id: string; date: string; channel: string; text: string }[]> = {
  billing: [
    { id: "u1", date: "Jan 15, 10:32 AM", channel: "whatsapp", text: "Customer requested status on duplicate charge and expressed dissatisfaction; escalation requested." },
    { id: "u2", date: "Jan 15, 11:05 AM", channel: "email", text: "Refund timeline communicated (3–5 business days); case escalated to billing for expedited review." },
    { id: "u3", date: "Jan 16, 09:02 AM", channel: "web", text: "Customer requested daily status updates pending refund confirmation." },
    { id: "u4", date: "Jan 17, 08:45 AM", channel: "email", text: "Refund initiated; transaction reference provided; advised to verify statement within 24–48 hours." },
  ],
  request: [
    { id: "u1", date: "Jan 12, 09:20 AM", channel: "email", text: "Feature request captured and added to product backlog with initial sizing." },
    { id: "u2", date: "Jan 12, 10:10 AM", channel: "chat", text: "Shared diagrams outlining current API constraints and suggested workaround." },
    { id: "u3", date: "Jan 13, 02:35 PM", channel: "web", text: "Customer uploaded sample workflow document for review." },
    { id: "u4", date: "Jan 14, 11:00 AM", channel: "email", text: "Product manager acknowledged request; timeline under evaluation." },
  ],
  bug: [
    { id: "u1", date: "Jan 18, 08:15 AM", channel: "email", text: "Error logs collected and attached to engineering ticket." },
    { id: "u2", date: "Jan 18, 09:05 AM", channel: "web", text: "Customer confirmed reproduction steps for webhook timeout." },
    { id: "u3", date: "Jan 18, 12:20 PM", channel: "chat", text: "Engineering triaged issue; patch under development." },
    { id: "u4", date: "Jan 19, 08:45 AM", channel: "email", text: "Fix deployed to sandbox; customer notified for validation." },
  ],
  complaint: [
    { id: "u1", date: "Jan 10, 09:45 AM", channel: "phone", text: "Complaint escalated after extended wait times were reported." },
    { id: "u2", date: "Jan 10, 10:05 AM", channel: "email", text: "Issued formal apology and shared revised SLA timeline." },
    { id: "u3", date: "Jan 11, 03:12 PM", channel: "web", text: "Customer acknowledged update but requested weekly progress reports." },
    { id: "u4", date: "Jan 12, 09:00 AM", channel: "email", text: "Account manager scheduled follow-up call to confirm satisfaction." },
  ],
  inquiry: [
    { id: "u1", date: "Jan 08, 11:30 AM", channel: "email", text: "Sent overview deck covering requested functionality." },
    { id: "u2", date: "Jan 08, 01:05 PM", channel: "chat", text: "Clarified pricing tiers and usage examples during live chat." },
    { id: "u3", date: "Jan 09, 09:40 AM", channel: "web", text: "Published curated FAQ links for quick reference." },
    { id: "u4", date: "Jan 09, 04:20 PM", channel: "email", text: "Customer confirmed understanding; awaiting internal approval." },
  ],
  feedback: [
    { id: "u1", date: "Jan 05, 10:10 AM", channel: "email", text: "Feedback form submitted highlighting onboarding friction points." },
    { id: "u2", date: "Jan 05, 12:30 PM", channel: "web", text: "Product ops tagged feedback for upcoming sprint review." },
    { id: "u3", date: "Jan 06, 09:50 AM", channel: "email", text: "Thanked customer and shared planned improvements." },
    { id: "u4", date: "Jan 07, 02:15 PM", channel: "chat", text: "Customer volunteered for future beta testing." },
  ],
};

// Local seeded cases (no API)
const seedCases: CaseItem[] = [
  {
    id: "C-1042",
    customer: { name: "Sarah Johnson", email: "sarah.johnson@gmail.com", avatar: "https://api.dicebear.com/7.x/avataaars/svg?seed=Sarah" },
    title: "Billing inquiry",
    snippet: "I was charged twice for last month...",
    priority: "urgent",
    status: "open",
    channel: "email",
    assignee: { name: "Nancy AI", avatar: "https://api.dicebear.com/7.x/bottts/svg?seed=Nancy" },
    startedAt: new Date(Date.now() - 1000 * 60 * 35).toISOString(),
    unread: 2,
    category: "Inquiry",
    documents: [
      { id: "d1", name: "invoice-aug.pdf", size: "234 KB", type: "pdf" },
      { id: "d2", name: "screenshot.png", size: "1.2 MB", type: "image" },
    ],
    history: [
      { id: "h1", at: new Date(Date.now() - 1000 * 60 * 32).toISOString(), actor: "Nancy AI", action: "Replied with billing policy" },
      { id: "h2", at: new Date(Date.now() - 1000 * 60 * 30).toISOString(), actor: "System", action: "Marked as urgent" },
    ],
  },
  {
    id: "C-1038",
    customer: { name: "Michael Chen", email: "michael.chen@acme.com", avatar: "https://api.dicebear.com/7.x/avataaars/svg?seed=Michael" },
    title: "Integration help",
    snippet: "Having trouble with OAuth callback...",
    priority: "high",
    status: "open",
    channel: "chat",
    assignee: { name: "Alex Rivera" },
    startedAt: new Date(Date.now() - 1000 * 60 * 90).toISOString(),
    unread: 0,
    category: "Request",
  },
  {
    id: "C-1032",
    customer: { name: "Emma Rodriguez", email: "emma.rodriguez@outlook.com" },
    title: "Access issue",
    snippet: "Can't access dashboard from mobile...",
    priority: "medium",
    status: "resolved",
    channel: "phone",
    assignee: { name: "Nancy AI" },
    startedAt: new Date(Date.now() - 1000 * 60 * 60 * 26).toISOString(),
    unread: 0,
    category: "Complaint",
  },
  {
    id: "C-1024",
    customer: { name: "David Kim", email: "d.kim@globex.com" },
    title: "Feature request",
    snippet: "Can we get export CSV?",
    priority: "low",
    status: "open",
    channel: "email",
    assignee: { name: "—" },
    startedAt: new Date(Date.now() - 1000 * 60 * 60 * 72).toISOString(),
    unread: 1,
    category: "Request",
  },
];

const useDebouncedValue = (value: string, delay = 300) => {
  const [debounced, setDebounced] = React.useState(value);
  React.useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(id);
  }, [value, delay]);
  return debounced;
};

const Cases = () => {
  const [status, setStatus] = React.useState<Status>("open");
  const [categories, setCategories] = React.useState<Set<Category>>(new Set());
  const [query, setQuery] = React.useState("");
  const debouncedQuery = useDebouncedValue(query, 300);
  const [dateOpen, setDateOpen] = React.useState(false);
  const [dateRange, setDateRange] = React.useState<{ from: Date | undefined; to: Date | undefined }>({ from: undefined, to: undefined });
  const [visible, setVisible] = React.useState({
    id: true,
    customer: true,
    title: true,
    priority: true,
    status: true,
    channel: true,
    assignee: true,
    started: true,
    unread: true,
    actions: true,
  });
  const [selected, setSelected] = React.useState<Set<string>>(new Set());
  const [selectAllFiltered, setSelectAllFiltered] = React.useState(false);
  const [deselectedIds, setDeselectedIds] = React.useState<Set<string>>(new Set());
  const [density, setDensity] = React.useState<"comfortable" | "compact">("comfortable");
  const [detailsOpen, setDetailsOpen] = React.useState(false);
  const [active, setActive] = React.useState<CaseItem | null>(null);
  const [helpOpen, setHelpOpen] = React.useState(false);
  const [focusedIndex, setFocusedIndex] = React.useState<number>(-1);

  // Column visibility persistence
  React.useEffect(() => {
    try {
      const raw = localStorage.getItem("cases.columns");
      if (raw) setVisible({ ...visible, ...JSON.parse(raw) });
    } catch {}
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  React.useEffect(() => {
    localStorage.setItem("cases.columns", JSON.stringify(visible));
  }, [visible]);

  const toggleCategory = (c: Category) => {
    setCategories((prev) => {
      const next = new Set(prev);
      if (next.has(c)) next.delete(c);
      else next.add(c);
      return next;
    });
  };

  const clearFilters = () => {
    setCategories(new Set());
    setQuery("");
    setDateRange({ from: undefined, to: undefined });
  };

  const pageSize = 25;
  const [page, setPage] = React.useState(1);
  React.useEffect(() => setPage(1), [status, categories, debouncedQuery, dateRange]);

  const filtered = React.useMemo(() => {
    let arr = seedCases.filter((c) => c.status === status);
    if (categories.size > 0) arr = arr.filter((c) => categories.has(c.category));
    if (debouncedQuery) {
      const q = debouncedQuery.toLowerCase();
      arr = arr.filter(
        (c) =>
          c.title.toLowerCase().includes(q) ||
          c.snippet.toLowerCase().includes(q) ||
          c.customer.name.toLowerCase().includes(q) ||
          c.customer.email.toLowerCase().includes(q)
      );
    }
    if (dateRange.from && dateRange.to) {
      arr = arr.filter((c) => {
        const d = new Date(c.startedAt).getTime();
        return d >= dateRange.from!.getTime() && d <= dateRange.to!.getTime();
      });
    }
    arr.sort((a, b) => new Date(b.startedAt).getTime() - new Date(a.startedAt).getTime());
    return arr;
  }, [status, categories, debouncedQuery, dateRange]);

  const pageItems = filtered.slice((page - 1) * pageSize, page * pageSize);
  const total = filtered.length;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));

  const allSelectedFiltered = selectAllFiltered && (total > 0);
  const selectedCount = allSelectedFiltered ? Math.max(0, total - deselectedIds.size) : selected.size;
  const allSelectedOnPage = pageItems.length > 0 && pageItems.every((i) => (allSelectedFiltered ? !deselectedIds.has(i.id) : selected.has(i.id)));
  const someSelectedOnPage = pageItems.some((i) => (allSelectedFiltered ? !deselectedIds.has(i.id) : selected.has(i.id))) && !allSelectedOnPage;

  const toggleHeaderSelect = () => {
    if (allSelectedFiltered) {
      // Turn off global selection
      setSelectAllFiltered(false);
      setDeselectedIds(new Set());
      setSelected(new Set());
    } else {
      // Select entire filtered set across pages
      setSelectAllFiltered(true);
      setDeselectedIds(new Set());
      setSelected(new Set());
    }
  };

  const openDetails = (item: CaseItem) => {
    setActive(item);
    setDetailsOpen(true);
  };

  const densityRow = density === "compact" ? "h-10" : "h-12";

  const ActiveIcon = ({ channel }: { channel: Channel }) => {
    const Icon = channelIcon(channel);
    return <Icon className="w-4 h-4" />;
  };

  const maskEmail = (email: string) => {
    // Mask by default; adjust when integrating RBAC
    const [user, domain] = email.split("@");
    const maskedUser = user.length <= 3 ? user[0] + "**" : user.slice(0, 3) + "***";
    return `${maskedUser}@${domain}`;
  };

  // Responsive switch ≤1023px → cards
  const [isNarrow, setIsNarrow] = React.useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 1023px)");
    const onChange = () => setIsNarrow(mq.matches);
    mq.addEventListener("change", onChange);
    setIsNarrow(mq.matches);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const dialogHeightClass = isNarrow ? "h-[95vh]" : "h-[80vh]";
  const detailScrollClass = isNarrow ? "h-[calc(95vh-170px)]" : "h-[calc(80vh-170px)]";
  const chatSectionClass = isNarrow ? "h-[calc(95vh-132px)]" : "h-[calc(80vh-132px)]";
  const caseType = React.useMemo(() => deriveCaseType(active), [active]);
  const diagnoses = AI_DIAGNOSES[caseType] || [];
  const aiActions = AI_ACTIONS_TAKEN[caseType] || [];
  const suggestedActions = SUGGESTED_ACTIONS[caseType] || [];
  const historyItems = CASE_TIMELINE[caseType] || [];
  const demoDocumentsByType: Record<CaseType, { name: string; size: string; type: string }[]> = {
    billing: [
      { name: "Invoice_2024-01-13_1025.pdf", size: "184 KB", type: "PDF" },
      { name: "Chat_Transcript_2024-01-15.txt", size: "62 KB", type: "Text" },
      { name: "Screenshot_Account_Settings.png", size: "1.3 MB", type: "Image" },
    ],
    request: [
      { name: "Feature_Request_Spec.docx", size: "96 KB", type: "Doc" },
      { name: "Use_case_notes.md", size: "12 KB", type: "Markdown" },
    ],
    bug: [
      { name: "Error_Logs_2024-01-16.json", size: "220 KB", type: "JSON" },
      { name: "Webhook_Response_Samples.zip", size: "3.4 MB", type: "Zip" },
    ],
    complaint: [
      { name: "Escalation_Report.pdf", size: "148 KB", type: "PDF" },
      { name: "Support_Call_Recording.mp3", size: "4.2 MB", type: "Audio" },
    ],
    inquiry: [
      { name: "Product_Brochure.pdf", size: "2.1 MB", type: "PDF" },
      { name: "FAQ_Link.txt", size: "4 KB", type: "Text" },
    ],
    feedback: [
      { name: "Feedback_Form_Response.pdf", size: "78 KB", type: "PDF" },
    ],
  };

  // Keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName?.toLowerCase();
      const isTyping = tag === 'input' || tag === 'textarea' || (e.target as HTMLElement)?.isContentEditable;
      if (isTyping) return;
      if (e.key === '?' || (e.shiftKey && e.key === '/')) {
        setHelpOpen(true); return;
      }
      if (!pageItems.length) return;
      if (e.key === 'ArrowDown') { e.preventDefault(); setFocusedIndex((i) => Math.min(pageItems.length - 1, i + 1)); }
      if (e.key === 'ArrowUp') { e.preventDefault(); setFocusedIndex((i) => Math.max(0, i - 1)); }
      if (e.key === 'Enter' && focusedIndex >= 0) { e.preventDefault(); openDetails(pageItems[focusedIndex]); }
      if (e.key.toLowerCase() === 'a') { e.preventDefault(); /* Assign stub */ }
      if (e.key.toLowerCase() === 'r') { e.preventDefault(); /* Resolve stub */ }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [pageItems, focusedIndex]);

  // Saved views persistence
  type SavedView = {
    name: string;
    status: Status;
    categories: Category[];
    q: string;
    from?: string;
    to?: string;
    columns?: typeof visible;
    density?: typeof density;
  };
  const [savedViews, setSavedViews] = React.useState<SavedView[]>([]);
  const [activeView, setActiveView] = React.useState<string | null>(null);
  useEffect(() => {
    try {
      const raw = localStorage.getItem('cases.savedViews');
      if (raw) setSavedViews(JSON.parse(raw));
    } catch {}
  }, []);
  const persistViews = (next: SavedView[]) => {
    setSavedViews(next);
    localStorage.setItem('cases.savedViews', JSON.stringify(next));
  };
  const saveCurrentAs = () => {
    const name = window.prompt('Save current filters as view name:');
    if (!name) return;
    const v: SavedView = {
      name,
      status,
      categories: Array.from(categories),
      q: query,
      from: dateRange.from?.toISOString(),
      to: dateRange.to?.toISOString(),
      columns: visible,
      density,
    };
    const next = [...savedViews.filter((s) => s.name !== name), v];
    persistViews(next);
    setActiveView(name);
  };
  const applyView = (name: string) => {
    const v = savedViews.find((s) => s.name === name);
    if (!v) return;
    setActiveView(name);
    setStatus(v.status);
    setCategories(new Set(v.categories));
    setQuery(v.q);
    setDateRange({ from: v.from ? new Date(v.from) : undefined, to: v.to ? new Date(v.to) : undefined });
    if (v.columns) setVisible(v.columns);
    if (v.density) setDensity(v.density);
  };
  const updateActiveView = () => {
    if (!activeView) return saveCurrentAs();
    const v: SavedView = {
      name: activeView,
      status,
      categories: Array.from(categories),
      q: query,
      from: dateRange.from?.toISOString(),
      to: dateRange.to?.toISOString(),
      columns: visible,
      density,
    };
    const next = [...savedViews.filter((s) => s.name !== activeView), v];
    persistViews(next);
  };

  return (
    <div className="min-h-screen bg-background flex">
      <Sidebar />
      <main className="flex-1 overflow-auto">
        <div className="w-full px-4 md:px-6 lg:px-8 py-6">
          {/* Page header with segmented control */}
          <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between mb-3">
            <div className="flex items-center gap-3">
              <div className="text-xl md:text-2xl font-semibold">Cases</div>
              <Badge variant="secondary" className="hidden md:inline-flex">{filtered.length} results</Badge>
            </div>
            <div>
              <Tabs value={status} onValueChange={(v) => setStatus(v as Status)}>
                <TabsList className="grid grid-cols-2 w-[220px]">
                  <TabsTrigger value="open">Open</TabsTrigger>
                  <TabsTrigger value="resolved">Resolved</TabsTrigger>
                </TabsList>
              </Tabs>
            </div>
          </div>

          {/* Toolbar */}
          <div className="sticky top-0 z-10 bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60 border-y border-border/60">
            <div className="py-2 flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
              <div className="flex items-center gap-2 flex-wrap">
                <Button variant="outline" size="sm" className={categories.has("Inquiry") ? "bg-primary/10 border-primary/30" : ""} onClick={() => toggleCategory("Inquiry")}>
                  Inquiries
                </Button>
                <Button variant="outline" size="sm" className={categories.has("Request") ? "bg-primary/10 border-primary/30" : ""} onClick={() => toggleCategory("Request")}>
                  Requests
                </Button>
                <Button variant="outline" size="sm" className={categories.has("Complaint") ? "bg-primary/10 border-primary/30" : ""} onClick={() => toggleCategory("Complaint")}>
                  Complaints
                </Button>
                {categories.size > 0 && (
                  <Button variant="ghost" size="sm" onClick={clearFilters} className="text-muted-foreground">Clear</Button>
                )}
                {/* Saved views (persisted) */}
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="outline" size="sm" className="gap-2">
                      <Layers3 className="w-4 h-4" />
                      {activeView ? activeView : 'Saved Views'}
                      <ChevronDown className="w-4 h-4" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="start">
                    <DropdownMenuLabel>My Views</DropdownMenuLabel>
                    {savedViews.length === 0 && (
                      <DropdownMenuItem disabled>No views saved</DropdownMenuItem>
                    )}
                    {savedViews.map((v) => (
                      <DropdownMenuItem key={v.name} onClick={() => applyView(v.name)}>{v.name}</DropdownMenuItem>
                    ))}
                    <DropdownMenuSeparator />
                    <DropdownMenuItem onClick={saveCurrentAs}>Save current as…</DropdownMenuItem>
                    <DropdownMenuItem onClick={updateActiveView} disabled={!activeView}>Update “{activeView || '—'}”</DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem onClick={() => { persistViews([]); setActiveView(null); }}>Clear all saved</DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
              <div className="flex items-center gap-2 w-full md:w-auto">
                {/* Date range */}
                <Popover open={dateOpen} onOpenChange={setDateOpen}>
                  <PopoverTrigger asChild>
                    <Button variant="outline" size="sm" className="gap-2 w-full md:w-auto justify-start">
                      <CalendarDays className="w-4 h-4" />
                      {dateRange.from && dateRange.to ? (
                        <span>
                          {dateRange.from.toLocaleDateString()} – {dateRange.to.toLocaleDateString()}
                        </span>
                      ) : (
                        <span>Date range</span>
                      )}
                    </Button>
                  </PopoverTrigger>
                  <PopoverContent className="w-auto p-3" align="end">
                    <div className="flex gap-3">
                      <div>
                        <div className="text-xs text-muted-foreground mb-1">Pick range</div>
                        <Calendar
                          initialFocus
                          mode="range"
                          selected={dateRange as any}
                          onSelect={(r: any) => setDateRange({ from: r?.from, to: r?.to })}
                          numberOfMonths={2}
                        />
                      </div>
                      <div className="w-44 space-y-2">
                        <div className="text-xs text-muted-foreground">Presets</div>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="w-full justify-start"
                          onClick={() => {
                            const now = new Date();
                            setDateRange({ from: now, to: now });
                          }}
                        >
                          Today
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="w-full justify-start"
                          onClick={() => {
                            const to = new Date();
                            const from = new Date(Date.now() - 1000 * 60 * 60 * 24 * 7);
                            setDateRange({ from, to });
                          }}
                        >
                          Last 7 days
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="w-full justify-start"
                          onClick={() => {
                            const to = new Date();
                            const from = new Date(Date.now() - 1000 * 60 * 60 * 24 * 30);
                            setDateRange({ from, to });
                          }}
                        >
                          Last 30 days
                        </Button>
                        <Separator />
                        <Button variant="outline" size="sm" className="w-full" onClick={() => setDateRange({ from: undefined, to: undefined })}>
                          Clear
                        </Button>
                      </div>
                    </div>
                  </PopoverContent>
                </Popover>

                {/* Search */}
                <div className="relative w-full md:w-64">
                  <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                  <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search name, email, text…" className="pl-9 bg-input border-0" />
                </div>

                {/* Column visibility */}
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="outline" size="sm" className="gap-2">
                      <ListFilter className="w-4 h-4" /> Columns
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    {Object.entries(visible).map(([key, val]) => (
                      <DropdownMenuCheckboxItem
                        key={key}
                        checked={val}
                        onCheckedChange={(v) => setVisible((prev) => ({ ...prev, [key]: Boolean(v) }))}
                      >
                        {key.charAt(0).toUpperCase() + key.slice(1)}
                      </DropdownMenuCheckboxItem>
                    ))}
                  </DropdownMenuContent>
                </DropdownMenu>

                {/* Density toggle */}
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="outline" size="sm" className="gap-2">
                      <MoreHorizontal className="w-4 h-4" /> View
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuCheckboxItem checked={density === "comfortable"} onCheckedChange={() => setDensity("comfortable")}>
                      Comfortable
                    </DropdownMenuCheckboxItem>
                    <DropdownMenuCheckboxItem checked={density === "compact"} onCheckedChange={() => setDensity("compact")}>
                      Compact
                    </DropdownMenuCheckboxItem>
                  </DropdownMenuContent>
                </DropdownMenu>

                {/* Help & Shortcuts */}
                <Dialog open={helpOpen} onOpenChange={setHelpOpen}>
                  <DialogTrigger asChild>
                    <Button variant="outline" size="sm" className="w-9 p-0">?</Button>
                  </DialogTrigger>
                  <DialogContent>
                    <DialogHeader>
                      <DialogTitle>Keyboard Shortcuts</DialogTitle>
                    </DialogHeader>
                    <div className="text-sm space-y-1">
                      <div>↑/↓: Navigate list</div>
                      <div>Enter: Open details</div>
                      <div>A: Assign</div>
                      <div>R: Resolve</div>
                      <div>?: Toggle this help</div>
                    </div>
                  </DialogContent>
                </Dialog>

                {/* Bulk actions (enabled on selection) */}
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button size="sm" disabled={selectedCount === 0} className="gap-2">
                      <User className="w-4 h-4" /> Bulk actions {selectedCount > 0 ? `(${selectedCount})` : ''}
                      <ChevronDown className="w-4 h-4" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuItem disabled={selectedCount === 0}>Assign…</DropdownMenuItem>
                    <DropdownMenuItem disabled={selectedCount === 0}>Resolve</DropdownMenuItem>
                    <DropdownMenuItem disabled={selectedCount === 0}>Mark as read</DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem disabled={selectedCount === 0}>Archive</DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
            </div>
          </div>

          {/* Table */}
          <div className="mt-3">
            {/* Error area reserved for future API errors */}
            <Card className="border border-border bg-card/60">
              {/* Table on wide screens; Card list on narrow */}
              {!isNarrow ? (
              <Table className="">
                <TableHeader className="sticky top-[52px] bg-card/90 backdrop-blur z-10">
                  <TableRow className="hover:bg-transparent">
                    <TableHead className="w-8">
                      <Checkbox checked={allSelectedOnPage || allSelectedFiltered} onCheckedChange={toggleHeaderSelect} aria-label="Select all filtered" className={someSelectedOnPage ? "data-[state=indeterminate]:opacity-100" : ""} />
                    </TableHead>
                    {visible.id && <TableHead className="w-[100px]">ID</TableHead>}
                    {visible.customer && <TableHead className="min-w-[200px]">Customer</TableHead>}
                    {visible.title && <TableHead className="min-w-[260px]">Title / Snippet</TableHead>}
                    {visible.priority && <TableHead>Priority</TableHead>}
                    {visible.status && <TableHead>Status</TableHead>}
                    {visible.channel && <TableHead>Channel</TableHead>}
                    {visible.assignee && <TableHead>Assignee</TableHead>}
                    {visible.started && <TableHead>Started</TableHead>}
                    {visible.unread && <TableHead className="text-right">Unread</TableHead>}
                    {visible.actions && <TableHead className="w-8" />}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {false ? (
                    [...Array(6)].map((_, i) => (
                      <TableRow key={i}>
                        <TableCell className="w-8"><Skeleton className="h-4 w-4 rounded" /></TableCell>
                        {visible.id && <TableCell><Skeleton className="h-4 w-16" /></TableCell>}
                        {visible.customer && (
                          <TableCell>
                            <div className="flex items-center gap-2">
                              <Skeleton className="h-8 w-8 rounded-full" />
                              <div className="space-y-1">
                                <Skeleton className="h-3 w-32" />
                                <Skeleton className="h-3 w-24" />
                              </div>
                            </div>
                          </TableCell>
                        )}
                        {visible.title && <TableCell><Skeleton className="h-3 w-64" /></TableCell>}
                        {visible.priority && <TableCell><Skeleton className="h-5 w-16 rounded-full" /></TableCell>}
                        {visible.status && <TableCell><Skeleton className="h-3 w-14" /></TableCell>}
                        {visible.channel && <TableCell><Skeleton className="h-4 w-4 rounded" /></TableCell>}
                        {visible.assignee && <TableCell><Skeleton className="h-3 w-24" /></TableCell>}
                        {visible.started && <TableCell><Skeleton className="h-3 w-20" /></TableCell>}
                        {visible.unread && <TableCell className="text-right"><Skeleton className="h-3 w-6 ml-auto" /></TableCell>}
                        {visible.actions && <TableCell />}
                      </TableRow>
                    ))
                  ) : pageItems.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={11}>
                        <div className="p-6 text-sm text-muted-foreground flex items-center justify-between">
                          <div>No cases match your filters.</div>
                          {(categories.size > 0 || query || (dateRange.from && dateRange.to)) && (
                            <Button variant="ghost" size="sm" onClick={clearFilters}>
                              Clear all
                            </Button>
                          )}
                        </div>
                      </TableCell>
                    </TableRow>
                  ) : (
                    pageItems.map((item) => (
                      <TableRow key={item.id} className={`${densityRow} cursor-pointer ${focusedIndex >= 0 && pageItems[focusedIndex]?.id === item.id ? 'ring-1 ring-primary' : ''}`} onClick={() => openDetails(item)}>
                        <TableCell className="w-8" onClick={(e) => e.stopPropagation()}>
                          <Checkbox
                            checked={allSelectedFiltered ? !deselectedIds.has(item.id) : selected.has(item.id)}
                            onCheckedChange={(v) => {
                              if (allSelectedFiltered) {
                                setDeselectedIds((prev) => {
                                  const next = new Set(prev);
                                  if (v) next.delete(item.id); else next.add(item.id);
                                  return next;
                                });
                              } else {
                                setSelected((prev) => {
                                  const next = new Set(prev);
                                  if (v) next.add(item.id); else next.delete(item.id);
                                  return next;
                                });
                              }
                            }}
                            aria-label={`Select ${item.id}`}
                          />
                        </TableCell>
                        {visible.id && <TableCell className="font-medium">{item.id}</TableCell>}
                        {visible.customer && (
                          <TableCell>
                            <div className="flex items-center gap-2">
                              <Avatar className="h-8 w-8">
                                {item.customer.avatar && <AvatarImage src={item.customer.avatar} />}
                                <AvatarFallback>{item.customer.name.slice(0, 2).toUpperCase()}</AvatarFallback>
                              </Avatar>
                              <div className="leading-tight">
                                <div className="text-sm font-medium">{item.customer.name}</div>
                                <div className="text-xs text-muted-foreground" title={'Email hidden'}>{maskEmail(item.customer.email)}</div>
                              </div>
                            </div>
                          </TableCell>
                        )}
                        {visible.title && (
                          <TableCell className="max-w-[520px]">
                            <div className="truncate">
                              <span className="font-medium mr-2">{item.title}</span>
                              <span className="text-muted-foreground">{item.snippet}</span>
                            </div>
                          </TableCell>
                        )}
                        {visible.priority && (
                          <TableCell>
                            <Badge className={`${priorityColor(item.priority)} capitalize`}>{item.priority}</Badge>
                          </TableCell>
                        )}
                        {visible.status && (
                          <TableCell className="capitalize">
                            {item.status}
                          </TableCell>
                        )}
                        {visible.channel && (
                          <TableCell>
                            <div className="inline-flex items-center gap-1 text-muted-foreground">
                              <ActiveIcon channel={item.channel} />
                              <span className="hidden xl:inline capitalize">{item.channel}</span>
                            </div>
                          </TableCell>
                        )}
                        {visible.assignee && (
                          <TableCell>
                            <div className="flex items-center gap-2">
                              <Avatar className="h-6 w-6">
                                {item.assignee?.avatar && <AvatarImage src={item.assignee.avatar} />}
                                <AvatarFallback>{(item.assignee?.name || "-").slice(0, 2).toUpperCase()}</AvatarFallback>
                              </Avatar>
                              <span className="text-sm">{item.assignee?.name || "—"}</span>
                            </div>
                          </TableCell>
                        )}
                        {visible.started && <TableCell>{formatRelative(item.startedAt)}</TableCell>}
                        {visible.unread && <TableCell className="text-right font-medium">{item.unread || 0}</TableCell>}
                        {visible.actions && (
                          <TableCell onClick={(e) => e.stopPropagation()}>
                            <DropdownMenu>
                              <DropdownMenuTrigger asChild>
                                <Button variant="ghost" size="icon">
                                  <EllipsisVertical className="w-4 h-4" />
                                </Button>
                              </DropdownMenuTrigger>
                              <DropdownMenuContent align="end">
                                <DropdownMenuItem onClick={() => openDetails(item)}>Open</DropdownMenuItem>
                                <DropdownMenuItem>Reassign…</DropdownMenuItem>
                                <DropdownMenuItem>Resolve</DropdownMenuItem>
                                <DropdownMenuSeparator />
                                <DropdownMenuItem>Archive</DropdownMenuItem>
                              </DropdownMenuContent>
                            </DropdownMenu>
                          </TableCell>
                        )}
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
              ) : (
                <div className="divide-y divide-border/60">
                  {false ? (
                    [...Array(6)].map((_, i) => (
                      <div key={i} className="p-3 flex items-start gap-3">
                        <Skeleton className="h-5 w-5 rounded" />
                        <div className="flex-1">
                          <Skeleton className="h-4 w-40 mb-2" />
                          <Skeleton className="h-3 w-64" />
                        </div>
                        <Skeleton className="h-4 w-10" />
                      </div>
                    ))
                  ) : pageItems.length === 0 ? (
                    <div className="p-6 text-sm text-muted-foreground">No cases match your filters.</div>
                  ) : (
                    pageItems.map((item) => (
                      <div key={item.id} className="p-3 flex items-start gap-3" onClick={() => openDetails(item)}>
                        <div onClick={(e) => e.stopPropagation()}>
                          <Checkbox
                            checked={allSelectedFiltered ? !deselectedIds.has(item.id) : selected.has(item.id)}
                            onCheckedChange={(v) => {
                              if (allSelectedFiltered) {
                                setDeselectedIds((prev) => { const next = new Set(prev); if (v) next.delete(item.id); else next.add(item.id); return next; });
                              } else {
                                setSelected((prev) => { const next = new Set(prev); if (v) next.add(item.id); else next.delete(item.id); return next; });
                              }
                            }}
                            aria-label={`Select ${item.id}`}
                          />
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <Badge className={`${priorityColor(item.priority)} capitalize`}>{item.priority}</Badge>
                            <div className="text-sm font-semibold truncate">{item.title}</div>
                          </div>
                          <div className="text-xs text-muted-foreground truncate">{item.snippet}</div>
                          <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
                            <Avatar className="h-5 w-5"><AvatarFallback>{item.customer.name.slice(0,2).toUpperCase()}</AvatarFallback></Avatar>
                            <span className="truncate">{item.customer.name}</span>
                            <span>•</span>
                            <span>{formatRelative(item.startedAt)}</span>
                          </div>
                        </div>
                        <div className="text-right">
                          <div className="inline-flex items-center gap-1 text-muted-foreground"><ActiveIcon channel={item.channel} /></div>
                          <div className="text-xs mt-1">{item.unread} unread</div>
                        </div>
                      </div>
                    ))
                  )}
                </div>
              )}
            </Card>
          </div>

          {/* Pagination */}
          <div className="sticky bottom-0 z-10 border-t border-border/60 bg-background/95 backdrop-blur mt-3 py-2">
            <Pagination>
              <PaginationContent>
                <PaginationItem>
                  <PaginationPrevious onClick={() => setPage((p) => Math.max(1, p - 1))} href="#" />
                </PaginationItem>
                {Array.from({ length: pageCount }).map((_, i) => (
                  <PaginationItem key={i}>
                    <PaginationLink href="#" isActive={page === i + 1} onClick={() => setPage(i + 1)}>
                      {i + 1}
                    </PaginationLink>
                  </PaginationItem>
                ))}
                <PaginationItem>
                  <PaginationNext onClick={() => setPage((p) => Math.min(pageCount, p + 1))} href="#" />
                </PaginationItem>
              </PaginationContent>
            </Pagination>
          </div>
        </div>
      </main>

      {/* Case details modal */}
      <Dialog open={detailsOpen} onOpenChange={setDetailsOpen}>
        <DialogContent className={`${isNarrow ? 'max-w-[95vw]' : 'max-w-4xl'} ${dialogHeightClass} max-h-[95vh] w-full p-0 gap-0 overflow-hidden`}>
          <div className="flex h-full flex-col">
            <div className="px-4 py-3 border-b border-border/60 bg-card/80 backdrop-blur">
              <DialogTitle className="sr-only">{active?.title || "Case details"}</DialogTitle>
              <div className="flex items-start justify-between gap-3 pr-12">
                <div className="flex flex-col gap-1">
                  <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">{active?.id}</span>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold">{active?.title}</span>
                    <Badge className={`${active ? priorityColor(active.priority) : ''} capitalize`}>{active?.priority}</Badge>
                  </div>
                </div>
                <div className="flex flex-wrap items-center justify-end gap-2 sm:flex-nowrap">
                  <Button size="sm" className="gap-1"><User className="w-4 h-4" /> Assign</Button>
                  <Button size="sm" className="gap-1"><CheckCircle2 className="w-4 h-4" /> Resolve</Button>
                </div>
              </div>
            </div>

            <Tabs defaultValue="overview" className="flex-1 flex flex-col">
              <div className="px-4 pt-2">
                <TabsList className="grid grid-cols-4 w-full">
                  <TabsTrigger value="overview">Overview</TabsTrigger>
                  <TabsTrigger value="documents">Documents</TabsTrigger>
                  <TabsTrigger value="history">History</TabsTrigger>
                  <TabsTrigger value="chat">Chat</TabsTrigger>
                </TabsList>
              </div>
              <div className="flex-1 min-h-0">
                <TabsContent value="overview" className="h-full">
                  <ScrollArea className={`${detailScrollClass} px-4 py-3`}>
                    {active && (
                      <div className="space-y-4">
                        <Card className="p-3 space-y-3">
                          <div>
                            <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Customer</div>
                            <div className="mt-3 flex items-center gap-3">
                              <Avatar className="h-9 w-9">
                                {active.customer.avatar && <AvatarImage src={active.customer.avatar} />}
                                <AvatarFallback>{active.customer.name.slice(0, 2).toUpperCase()}</AvatarFallback>
                              </Avatar>
                              <div>
                                <div className="text-sm font-semibold">{active.customer.name}</div>
                                <div className="text-xs text-muted-foreground">{active.customer.email}</div>
                              </div>
                            </div>
                          </div>
                          <Separator />
                          <div className="grid grid-cols-2 gap-3 text-sm">
                            <div className="space-y-1">
                              <div className="text-xs text-muted-foreground">Status</div>
                              <div className="capitalize">{active.status}</div>
                            </div>
                            <div className="space-y-1">
                              <div className="text-xs text-muted-foreground">Started</div>
                              <div>{formatRelative(active.startedAt)}</div>
                            </div>
                            <div className="space-y-1">
                              <div className="text-xs text-muted-foreground">Channel</div>
                              <div className="inline-flex items-center gap-1 text-muted-foreground">
                                <ActiveIcon channel={active.channel} />
                                <span className="capitalize">{active.channel}</span>
                              </div>
                            </div>
                            <div className="space-y-1">
                              <div className="text-xs text-muted-foreground">Assignee</div>
                              <div>{active.assignee?.name || "—"}</div>
                            </div>
                          </div>
                        </Card>

                        <Card className="p-3 space-y-4">
                          {[
                            { title: "AI Diagnoses", lines: diagnoses, empty: "No diagnoses available." },
                            { title: "AI Actions Taken", lines: aiActions, empty: "No AI actions recorded." },
                            { title: "Suggested Actions", lines: suggestedActions, empty: "No pending actions." },
                          ].map((section) => (
                            <div key={section.title} className="space-y-2">
                              <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{section.title}</span>
                              <div className="rounded-lg border border-border/60 bg-muted/15 px-3 py-2 space-y-2">
                                {section.lines.length > 0 ? (
                                  section.lines.map((line, lineIdx) => (
                                    <div key={`${section.title}-${lineIdx}`} className="grid grid-cols-[auto,1fr] gap-x-2 gap-y-1 text-sm leading-snug text-foreground">
                                      <span className="mt-[7px] h-1.5 w-1.5 rounded-full bg-muted-foreground/50" />
                                      <span>{line}</span>
                                    </div>
                                  ))
                                ) : (
                                  <div className="text-sm text-muted-foreground">{section.empty}</div>
                                )}
                              </div>
                            </div>
                          ))}
                        </Card>
                      </div>
                    )}
                  </ScrollArea>
                </TabsContent>

                <TabsContent value="documents" className="h-full">
                  <ScrollArea className={`${detailScrollClass} px-4 py-3`}>
                    <div className="space-y-3">
                      <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Documents</div>
                      <div className="space-y-2">
                        {(demoDocumentsByType[caseType] || []).length === 0 ? (
                          <div className="text-sm text-muted-foreground">No documents attached.</div>
                        ) : (
                          demoDocumentsByType[caseType].map((f, idx) => (
                            <div key={`${f.name}-${idx}`} className="flex items-center gap-3 rounded-lg border border-border/60 bg-muted/20 px-3 py-2">
                              <div className="flex h-9 w-9 items-center justify-center rounded-md bg-muted">
                                <FileText className="w-4 h-4 text-muted-foreground" />
                              </div>
                              <div className="flex-1 min-w-0">
                                <div className="text-sm font-medium truncate">{f.name}</div>
                                <div className="text-xs text-muted-foreground">{f.type} • {f.size}</div>
                              </div>
                              <div className="flex items-center gap-2">
                                <Button variant="ghost" size="sm">Open</Button>
                                <Button variant="secondary" size="sm">Download</Button>
                              </div>
                            </div>
                          ))
                        )}
                      </div>
                    </div>
                  </ScrollArea>
                </TabsContent>

                <TabsContent value="history" className="h-full">
                  <ScrollArea className={`${detailScrollClass} px-4 py-3`}>
                    <Card className="p-3 space-y-3 border border-border/60">
                      <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Case Updates</div>
                      <div className="space-y-3">
                        {historyItems.length === 0 ? (
                          <div className="text-sm text-muted-foreground">No history entries.</div>
                        ) : (
                          historyItems.map((item, idx) => {
                            const isLast = idx === historyItems.length - 1;
                            return (
                              <React.Fragment key={item.id}>
                                <div className="space-y-2">
                                  <div className="text-[11px] text-muted-foreground">{item.date}</div>
                                  <div className="text-sm text-foreground">{item.text}</div>
                                </div>
                                {!isLast && <div className="my-3 h-px bg-gradient-to-r from-transparent via-border/60 to-transparent" />}
                              </React.Fragment>
                            );
                          })
                        )}
                      </div>
                    </Card>
                  </ScrollArea>
                </TabsContent>

                <TabsContent value="chat" className="h-full">
                  <div className={`${chatSectionClass} flex flex-col`}>
                    <div className="px-4 py-2 border-b border-border/60 bg-card/70 backdrop-blur text-xs text-muted-foreground">Read-only transcript</div>
                    <ScrollArea className="flex-1 px-4 py-3">
                      <div className="space-y-3">
                        <div className="flex items-start gap-2">
                          <Avatar className="h-7 w-7"><AvatarFallback>CU</AvatarFallback></Avatar>
                          <div className="rounded-lg border border-border/60 p-2 max-w-[75%]"><div className="text-sm">Hi, I was charged twice for last month.</div></div>
                        </div>
                        <div className="flex items-start gap-2 justify-end">
                          <div className="rounded-lg border border-border/60 p-2 max-w-[75%] bg-primary/10"><div className="text-sm">Sorry about that! Let me check your invoice.</div></div>
                          <Avatar className="h-7 w-7"><AvatarFallback>AG</AvatarFallback></Avatar>
                        </div>
                      </div>
                    </ScrollArea>
                  </div>
                </TabsContent>
              </div>
            </Tabs>

            <div className="px-4 py-3 border-t border-border/60 flex items-center justify-between">
              <div className="text-xs text-muted-foreground">Actions may be restricted by permissions.</div>
              <div className="flex items-center gap-2">
                <Button variant="outline" size="sm">Reassign…</Button>
                <Button size="sm" className="gap-1"><CheckCircle2 className="w-4 h-4" /> Resolve</Button>
              </div>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Mobile bulk actions bar */}
      {isNarrow && selectedCount > 0 && (
        <div className="fixed bottom-16 left-0 right-0 z-20 px-4">
          <div className="mx-auto max-w-4xl rounded-lg border border-border bg-card/95 backdrop-blur p-2 shadow-lg flex items-center justify-between">
            <div className="text-sm">{selectedCount} selected</div>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm">Assign</Button>
              <Button size="sm">Resolve</Button>
              <Button variant="outline" size="sm">Archive</Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default Cases;
