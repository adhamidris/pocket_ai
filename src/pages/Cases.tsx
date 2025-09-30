import React from "react";
import { Link } from "react-router-dom";
import {
  Building2,
  CalendarDays,
  ChevronDown,
  ChevronRight,
  Clock,
  EllipsisVertical,
  Filter,
  Inbox,
  ListFilter,
  Mail,
  MessageCircle,
  Search,
  User,
  Users,
  Plus,
  Upload,
  CheckCircle2,
  AlertTriangle,
  Phone,
  FileText,
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
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
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { useEffect } from "react";
import { cn } from "@/lib/utils";

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
    { label: "Leads", to: "/dashboard/leads" },
    { label: "Customers", to: "/dashboard/customers" },
    { label: "Agents", to: "/dashboard/agents" },
    { label: "Knowledge", to: "/dashboard/knowledge" },
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
      return "bg-muted/40 text-muted-foreground border border-muted-foreground/20";
    case "medium":
      return "bg-blue-500/15 text-blue-600 dark:text-blue-300 border border-blue-500/25";
    case "high":
      return "bg-amber-400/20 text-amber-700 dark:text-amber-200 border border-amber-400/30";
    case "urgent":
      return "bg-rose-500/20 text-rose-600 dark:text-rose-200 border border-rose-500/30";
  }
};

const statusColor = (s: Status) => {
  switch (s) {
    case "open":
      return "bg-emerald-500/15 text-emerald-600 dark:text-emerald-300 border border-emerald-500/30";
    case "resolved":
      return "bg-muted/40 text-muted-foreground border border-muted-foreground/20";
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

const formatCustomerId = (item: CaseItem | null) => {
  if (!item) return "CUST-0001";
  const digits = item.id.replace(/\D/g, "");
  const numeric = digits ? parseInt(digits, 10) : 1;
  return `CUST-${numeric.toString().padStart(4, "0")}`;
};

const StatCard = React.memo(
  ({
    label,
    value,
    delta,
  }: {
    label: string;
    value: string;
    delta?: string;
  }) => {
    return (
      <Card className="border border-border/70 bg-muted/70 backdrop-blur">
        <div className="p-5 space-y-2">
          <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/70">{label}</span>
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-3xl font-semibold tracking-tight text-foreground">{value}</span>
            {delta && <span className="text-xs font-medium text-muted-foreground">{delta}</span>}
          </div>
        </div>
      </Card>
    );
  }
);

const Cases = () => {
  const [statusFilter, setStatusFilter] = React.useState<"all" | Status>("all");
  const [categoryFilter, setCategoryFilter] = React.useState<"all" | Category>("all");
  const [query, setQuery] = React.useState("");
  const debouncedQuery = useDebouncedValue(query, 300);
  const [dateFilter, setDateFilter] = React.useState<"7" | "14" | "30" | "all">("7");
  const [visible, setVisible] = React.useState({
    id: true,
    customer: true,
    title: true,
    description: true,
    type: true,
    priority: true,
    status: true,
    channel: false,
    assignee: false,
    started: true,
    unread: false,
    actions: true,
  });
  const [selected, setSelected] = React.useState<Set<string>>(new Set());
  const [selectAllFiltered, setSelectAllFiltered] = React.useState(false);
  const [deselectedIds, setDeselectedIds] = React.useState<Set<string>>(new Set());
  const [density, setDensity] = React.useState<"comfortable" | "compact">("comfortable");
  const [detailsOpen, setDetailsOpen] = React.useState(false);
  const [viewportWidth, setViewportWidth] = React.useState(() => (typeof window !== "undefined" ? window.innerWidth : 0));
  const [active, setActive] = React.useState<CaseItem | null>(null);
  
  const [focusedIndex, setFocusedIndex] = React.useState<number>(-1);

  const openCasesCount = React.useMemo(() => seedCases.filter((c) => c.status === "open").length, []);
  const urgentCasesCount = React.useMemo(() => seedCases.filter((c) => c.priority === "urgent" && c.status === "open").length, []);
  const avgOpenAge = React.useMemo(() => {
    const openCases = seedCases.filter((c) => c.status === "open");
    if (!openCases.length) return "—";
    const now = Date.now();
    const avgMs = openCases.reduce((acc, item) => acc + (now - new Date(item.startedAt).getTime()), 0) / openCases.length;
    const hours = Math.max(1, Math.round(avgMs / (1000 * 60 * 60)));
    return `${hours}h`;
  }, []);

  // Column visibility persistence
  React.useEffect(() => {
    try {
      const raw = localStorage.getItem("cases.columns");
      if (raw) {
        const parsed = JSON.parse(raw);
        setVisible((prev) => ({
          ...prev,
          ...parsed,
          // enforce defaults for removed/added columns
          assignee: false,
          unread: false,
          channel: false,
          description: true,
          type: true,
        }));
      }
    } catch {}
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  React.useEffect(() => {
    localStorage.setItem("cases.columns", JSON.stringify(visible));
  }, [visible]);

  const categoryOptions: { value: "all" | Category; label: string }[] = React.useMemo(
    () => [
      { value: "all", label: "All types" },
      { value: "Inquiry", label: "Inquiries" },
      { value: "Request", label: "Requests" },
      { value: "Complaint", label: "Complaints" },
    ],
    []
  );

  const clearFilters = () => {
    setCategoryFilter("all");
    setQuery("");
    setDateFilter("all");
  };

  const pageSize = 25;
  type SortKey = 'priority' | 'status' | 'started' | null;
  const [sortKey, setSortKey] = React.useState<SortKey>('started');
  const [sortDir, setSortDir] = React.useState<'asc' | 'desc'>('desc');
  const toggleSort = (key: Exclude<SortKey, null>) => {
    setSortDir((prev) => (sortKey === key ? (prev === 'asc' ? 'desc' : 'asc') : (key === 'started' ? 'desc' : 'desc')));
    setSortKey(key);
    setPage(1);
  };
  const [page, setPage] = React.useState(1);
  React.useEffect(() => setPage(1), [statusFilter, categoryFilter, debouncedQuery, dateFilter]);

  const filtered = React.useMemo(() => {
    let arr = seedCases;
    if (statusFilter !== "all") {
      arr = arr.filter((c) => c.status === statusFilter);
    }
    if (categoryFilter !== "all") {
      arr = arr.filter((c) => c.category === categoryFilter);
    }
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
    if (dateFilter !== "all") {
      const days = Number(dateFilter);
      const cutoff = Date.now() - days * 24 * 60 * 60 * 1000;
      arr = arr.filter((c) => new Date(c.startedAt).getTime() >= cutoff);
    }
    arr.sort((a, b) => new Date(b.startedAt).getTime() - new Date(a.startedAt).getTime());
    return arr;
  }, [statusFilter, categoryFilter, debouncedQuery, dateFilter]);

  const sorted = React.useMemo(() => {
    const arr = [...filtered];
    if (!sortKey) return arr;
    const dir = sortDir === 'asc' ? 1 : -1;
    if (sortKey === 'started') {
      return arr.sort((a, b) => (new Date(a.startedAt).getTime() - new Date(b.startedAt).getTime()) * dir);
    }
    if (sortKey === 'priority') {
      const rank: Record<Priority, number> = { low: 0, medium: 1, high: 2, urgent: 3 };
      return arr.sort((a, b) => (rank[a.priority] - rank[b.priority]) * dir);
    }
    if (sortKey === 'status') {
      const rank: Record<Status, number> = { resolved: 0, open: 1 };
      return arr.sort((a, b) => (rank[a.status] - rank[b.status]) * dir);
    }
    return arr;
  }, [filtered, sortKey, sortDir]);

  const pageItems = sorted.slice((page - 1) * pageSize, page * pageSize);
  const total = sorted.length;
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

  const densityRow = density === "compact" ? "h-11" : "h-14";

  const ActiveIcon = ({ channel }: { channel: Channel }) => {
    const Icon = channelIcon(channel);
    return <Icon className="w-4 h-4" />;
  };

  const maskEmail = (email: string) => email;

  // Responsive switch ≤1023px → cards
  const [isNarrow, setIsNarrow] = React.useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 1023px)");
    const onChange = () => setIsNarrow(mq.matches);
    mq.addEventListener("change", onChange);
    setIsNarrow(mq.matches);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  React.useEffect(() => {
    if (typeof window === "undefined") return;
    const onResize = () => setViewportWidth(window.innerWidth);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const panelWidth = React.useMemo(() => {
    if (isNarrow || !viewportWidth) return undefined;
    const sidebar = 264;
    const gutter = 24;
    const calculated = Math.max(viewportWidth - sidebar - gutter, 560);
    return `${Math.min(Math.round(calculated), 1080)}px`;
  }, [isNarrow, viewportWidth]);
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

  const SectionBlock = ({
    title,
    children,
    cardClassName,
    headingClassName,
  }: {
    title: string;
    children: React.ReactNode;
    cardClassName?: string;
    headingClassName?: string;
  }) => (
    <div className="space-y-2">
      <div className={cn("text-xs font-semibold uppercase tracking-wide text-muted-foreground", headingClassName)}>
        {title}
      </div>
      <Card className={cn("border border-border/70 bg-muted/70 backdrop-blur px-3 py-3", cardClassName)}>{children}</Card>
    </div>
  );

  // Keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName?.toLowerCase();
      const isTyping = tag === 'input' || tag === 'textarea' || (e.target as HTMLElement)?.isContentEditable;
      if (isTyping) return;
      // help dialog removed
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

  return (
    <div className="min-h-screen bg-background flex">
      <Sidebar />
      <main className="flex-1 overflow-auto">
        <div className="w-full px-4 md:px-6 lg:px-8 py-6">
          <section className="flex flex-col gap-6">
            <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
              <div className="flex items-center gap-3">
                <div className="text-xl md:text-2xl font-semibold">Cases</div>
                <Badge variant="secondary" className="hidden md:inline-flex">{filtered.length} results</Badge>
              </div>
              <div className="flex items-center gap-2">
                <Button className="gap-2">
                  <Plus className="w-4 h-4" />
                  New Case
                </Button>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="outline" className="gap-2 text-sm">
                      <Upload className="w-4 h-4" />
                      Import Cases
                      <ChevronDown className="w-4 h-4 text-muted-foreground" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end" className="w-44">
                    <DropdownMenuItem>Upload CSV</DropdownMenuItem>
                    <DropdownMenuItem>Sync HubSpot</DropdownMenuItem>
                    <DropdownMenuItem>Connect Salesforce</DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem>API ingest</DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <StatCard label="Open cases" value={`${openCasesCount}`} delta="Target < 15" />
              <StatCard label="Urgent cases" value={`${urgentCasesCount}`} delta={urgentCasesCount ? `${urgentCasesCount} need escalation` : "All clear"} />
              <StatCard label="Avg. time open" value={avgOpenAge} delta="Goal < 12h" />
            </div>

            {/* Toolbar */}
            <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
              <div className="flex flex-col gap-3 md:flex-row md:items-center md:flex-wrap">
                <div className="relative w-full md:w-64">
                  <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="Search name, email, text…"
                    className="pl-9 border border-border/60 bg-muted/70 focus-visible:ring-0 focus-visible:border-border"
                  />
                </div>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="outline" className="gap-2 text-sm focus-visible:outline-none focus-visible:ring-0">
                      <Building2 className="w-4 h-4" />
                      {statusFilter === "all" ? "All Cases" : statusFilter === "open" ? "Open" : "Resolved"}
                      <ChevronDown className="w-4 h-4 text-muted-foreground" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="start" className="w-40">
                    <DropdownMenuItem onClick={() => setStatusFilter("all")}>All Cases</DropdownMenuItem>
                    <DropdownMenuItem onClick={() => setStatusFilter("open")}>Open</DropdownMenuItem>
                    <DropdownMenuItem onClick={() => setStatusFilter("resolved")}>Resolved</DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="outline" className="gap-2 text-sm focus-visible:outline-none focus-visible:ring-0">
                      <Filter className="w-4 h-4" />
                      {categoryOptions.find((option) => option.value === categoryFilter)?.label ?? "All types"}
                      <ChevronDown className="w-4 h-4 text-muted-foreground" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="start" className="w-44">
                    {categoryOptions.map((option) => (
                      <DropdownMenuItem key={option.value} onClick={() => setCategoryFilter(option.value)}>
                        {option.label}
                      </DropdownMenuItem>
                    ))}
                  </DropdownMenuContent>
                </DropdownMenu>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="outline" className="gap-2 text-sm focus-visible:outline-none focus-visible:ring-0">
                      <CalendarDays className="w-4 h-4" />
                      {dateFilter === "all" ? "All time" : `Last ${dateFilter} days`}
                      <ChevronDown className="w-4 h-4 text-muted-foreground" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="start" className="w-36">
                    <DropdownMenuItem onClick={() => setDateFilter("7")}>Last 7 days</DropdownMenuItem>
                    <DropdownMenuItem onClick={() => setDateFilter("14")}>Last 14 days</DropdownMenuItem>
                    <DropdownMenuItem onClick={() => setDateFilter("30")}>Last 30 days</DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem onClick={() => setDateFilter("all")}>All time</DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
              <div className="flex items-center gap-2">
                {/* Column visibility */}
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="outline" className="gap-2 text-sm focus-visible:outline-none focus-visible:ring-0">
                      <ListFilter className="w-4 h-4" /> Columns
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    {[
                      ["id", "Case ID"],
                      ["priority", "Priority"],
                      ["type", "Type"],
                      ["title", "Case Title"],
                      ["description", "Description"],
                      ["status", "Status"],
                      ["customer", "Customer"],
                      ["started", "Started"],
                    ].map(([key, label]) => (
                      <DropdownMenuCheckboxItem
                        key={key}
                        checked={(visible as any)[key]}
                        onCheckedChange={(v) => setVisible((prev) => ({ ...(prev as any), [key]: Boolean(v) }))}
                      >
                        {label}
                      </DropdownMenuCheckboxItem>
                    ))}
                  </DropdownMenuContent>
                </DropdownMenu>

                {selectedCount > 0 && (
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button size="sm" className="gap-2">
                        <User className="w-4 h-4" /> Bulk actions ({selectedCount})
                        <ChevronDown className="w-4 h-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem>Assign…</DropdownMenuItem>
                      <DropdownMenuItem>Resolve</DropdownMenuItem>
                      <DropdownMenuItem>Mark as read</DropdownMenuItem>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem>Archive</DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                )}
              </div>
            </div>

          {/* Table */}
          <div className="grid grid-cols-12 gap-4">
            <div className="col-span-12 space-y-4">
              {/* Error area reserved for future API errors */}
              <Card className="border border-border/70 bg-card/90 shadow-sm backdrop-blur">
                {/* Table on wide screens; Card list on narrow */}
                {!isNarrow ? (
                <div className="relative overflow-x-auto overflow-y-auto max-h-[70vh]">
                  <Table className="rounded-xl border border-border/70 bg-background/60 shadow-sm backdrop-blur [&_th]:px-3 [&_td]:px-3 [&_th:first-child]:pl-4 [&_td:first-child]:pl-4 [&_th:last-child]:pr-4 [&_td:last-child]:pr-4 [&_th]:py-3 [&_td]:py-3">
                  <TableHeader className="sticky top-0 z-10 bg-muted/70 backdrop-blur border-b border-border/80">
                    <TableRow className="hover:bg-transparent">
                      <TableHead className="w-8">
                        <Checkbox checked={allSelectedOnPage || allSelectedFiltered} onCheckedChange={toggleHeaderSelect} aria-label="Select all filtered" className={someSelectedOnPage ? "data-[state=indeterminate]:opacity-100" : ""} />
                      </TableHead>
                      {visible.id && <TableHead className="w-[100px]">Case ID</TableHead>}
                      {visible.priority && (
                        <TableHead className="w-[110px]">
                          <button
                            type="button"
                            className="inline-flex items-center gap-1 font-medium hover:text-foreground"
                            onClick={() => toggleSort('priority')}
                            aria-label="Sort by priority"
                          >
                            Priority
                            {sortKey === 'priority' ? (
                              sortDir === 'asc' ? <ArrowUp className="w-3.5 h-3.5" /> : <ArrowDown className="w-3.5 h-3.5" />
                            ) : (
                              <ArrowUpDown className="w-3.5 h-3.5 opacity-50" />
                            )}
                          </button>
                        </TableHead>
                      )}
                      {visible.type && <TableHead className="w-[120px]">Type</TableHead>}
                      {visible.title && <TableHead className="w-[220px]">Case Title</TableHead>}
                      {visible.description && <TableHead className="w-[320px]">Description</TableHead>}
                      {visible.status && (
                        <TableHead className="w-[120px]">
                          <button
                            type="button"
                            className="inline-flex items-center gap-1 font-medium hover:text-foreground"
                            onClick={() => toggleSort('status')}
                            aria-label="Sort by status"
                          >
                            Status
                            {sortKey === 'status' ? (
                              sortDir === 'asc' ? <ArrowUp className="w-3.5 h-3.5" /> : <ArrowDown className="w-3.5 h-3.5" />
                            ) : (
                              <ArrowUpDown className="w-3.5 h-3.5 opacity-50" />
                            )}
                          </button>
                        </TableHead>
                      )}
                      {visible.customer && <TableHead className="min-w-[200px]">Customer</TableHead>}
                      {visible.channel && <TableHead className="w-[120px]">Channel</TableHead>}
                      {visible.started && (
                        <TableHead className="w-[120px] text-right">
                          <button
                            type="button"
                            className="flex w-full items-center justify-end gap-1 font-medium hover:text-foreground"
                            onClick={() => toggleSort('started')}
                            aria-label="Sort by started"
                          >
                            Started
                            {sortKey === 'started' ? (
                              sortDir === 'asc' ? <ArrowUp className="w-3.5 h-3.5" /> : <ArrowDown className="w-3.5 h-3.5" />
                            ) : (
                              <ArrowUpDown className="w-3.5 h-3.5 opacity-50" />
                            )}
                          </button>
                        </TableHead>
                      )}
                      {visible.actions && <TableHead className="w-8" />}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                  {false ? (
                    [...Array(6)].map((_, i) => (
                      <TableRow key={i}>
                        <TableCell className="w-8"><Skeleton className="h-4 w-4 rounded" /></TableCell>
                        {visible.id && <TableCell><Skeleton className="h-4 w-16" /></TableCell>}
                        {visible.priority && <TableCell><Skeleton className="h-5 w-16 rounded-full" /></TableCell>}
                        {visible.type && <TableCell><Skeleton className="h-3 w-16" /></TableCell>}
                        {visible.title && <TableCell><Skeleton className="h-3 w-48" /></TableCell>}
                        {visible.description && <TableCell><Skeleton className="h-3 w-64" /></TableCell>}
                        {visible.status && <TableCell><Skeleton className="h-3 w-14" /></TableCell>}
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
                        {visible.channel && <TableCell><Skeleton className="h-4 w-4 rounded" /></TableCell>}
                        {visible.started && <TableCell><Skeleton className="h-3 w-20" /></TableCell>}
                        {visible.actions && <TableCell />}
                      </TableRow>
                    ))
                  ) : pageItems.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={
                        1 +
                        (visible.id ? 1 : 0) +
                        (visible.customer ? 1 : 0) +
                        (visible.title ? 1 : 0) +
                        (visible.description ? 1 : 0) +
                        (visible.type ? 1 : 0) +
                        (visible.priority ? 1 : 0) +
                        (visible.status ? 1 : 0) +
                        (visible.channel ? 1 : 0) +
                        (visible.started ? 1 : 0) +
                        (visible.actions ? 1 : 0)
                      }>
                        <div className="p-6 text-sm text-muted-foreground flex items-center justify-between">
                          <div>No cases match your filters.</div>
                          {(categoryFilter !== "all" || debouncedQuery || dateFilter !== "all") && (
                            <Button variant="ghost" size="sm" onClick={clearFilters}>
                              Clear all
                            </Button>
                          )}
                        </div>
                      </TableCell>
                    </TableRow>
                  ) : (
                    pageItems.map((item) => (
                      <TableRow key={item.id} className={cn(
                        densityRow,
                        "group cursor-pointer bg-transparent transition-colors hover:bg-muted/60 dark:hover:bg-white/10 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-offset-0 focus-visible:ring-primary",
                        focusedIndex >= 0 && pageItems[focusedIndex]?.id === item.id ? "ring-1 ring-primary" : ""
                      )} onClick={() => openDetails(item)}>
                        <TableCell className="w-10" onClick={(e) => e.stopPropagation()}>
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
                        {visible.id && <TableCell className="font-mono text-xs text-foreground/70">{item.id}</TableCell>}
                        {visible.priority && (
                          <TableCell className="whitespace-nowrap">
                            <Badge className={cn("capitalize", priorityColor(item.priority))}>
                              {item.priority}
                            </Badge>
                          </TableCell>
                        )}
                        {visible.type && (
                          <TableCell className="whitespace-nowrap">
                            <span className="text-foreground/80 capitalize font-medium">{item.category}</span>
                          </TableCell>
                        )}
                        {visible.title && (
                          <TableCell className="w-[220px] pr-1">
                            <div className="truncate font-semibold text-foreground">{item.title}</div>
                          </TableCell>
                        )}
                        {visible.description && (
                          <TableCell className="w-[320px] pl-1">
                            <div className="truncate text-foreground/70">{item.snippet}</div>
                          </TableCell>
                        )}
                        {visible.status && (
                          <TableCell className="whitespace-nowrap">
                            <Badge className={cn("capitalize", statusColor(item.status))}>{item.status}</Badge>
                          </TableCell>
                        )}
                        {visible.customer && (
                          <TableCell className="max-w-[240px]">
                            <div className="flex items-center gap-3 min-w-0">
                              <Avatar className="h-9 w-9 shadow-sm">
                                {item.customer.avatar && <AvatarImage src={item.customer.avatar} />}
                                <AvatarFallback>{item.customer.name.slice(0, 2).toUpperCase()}</AvatarFallback>
                              </Avatar>
                              <div className="leading-tight min-w-0">
                                <div className="text-sm font-semibold truncate text-foreground">{item.customer.name}</div>
                                <div className="text-xs text-foreground/60 truncate" title={'Email hidden'}>{maskEmail(item.customer.email)}</div>
                              </div>
                            </div>
                          </TableCell>
                        )}
                        {visible.channel && (
                          <TableCell>
                            <div className="inline-flex items-center gap-2 text-foreground/70">
                              <span className="inline-flex h-7 w-7 items-center justify-center rounded-full border border-border/80 bg-muted/50 text-foreground/70">
                                <ActiveIcon channel={item.channel} />
                              </span>
                              <span className="hidden xl:inline capitalize font-medium">{item.channel}</span>
                            </div>
                          </TableCell>
                        )}
                        {visible.started && <TableCell className="text-right tabular-nums whitespace-nowrap text-foreground/70">{formatRelative(item.startedAt)}</TableCell>}
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
              </div>
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
          </div>

          </section>
        </div>
      </main>

      {/* Case details modal */}
      <Sheet open={detailsOpen} onOpenChange={setDetailsOpen}>
        <SheetContent
          side="right"
          className={`${isNarrow ? 'w-screen' : 'sm:max-w-none max-w-none'} p-0 bg-card/95 backdrop-blur border-l border-border/60`}
          style={isNarrow ? undefined : panelWidth ? { width: panelWidth } : undefined}
        >
          <div className="flex h-full flex-col">
            <div className="px-4 py-3 border-b border-border/60 bg-card/80 backdrop-blur">
              <SheetHeader>
                <SheetTitle className="sr-only">{active?.title || "Case details"}</SheetTitle>
              </SheetHeader>
              <div className="flex flex-col gap-1 pr-12">
                <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide">{active?.id}</span>
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="font-semibold text-base sm:text-lg">{active?.title}</span>
                  <Badge className={`${active ? priorityColor(active.priority) : ''} capitalize`}>{active?.priority}</Badge>
                  {active && <Badge className={`${statusColor(active.status)} capitalize`}>{active.status}</Badge>}
                  <div className="ml-auto text-xs text-muted-foreground">
                    {active && `Started ${formatRelative(active.startedAt)}`}
                  </div>
                </div>
              </div>
            </div>

            <Tabs defaultValue="overview" className="flex-1 flex flex-col">
              <div className="px-4 pt-3">
                <TabsList className="grid grid-cols-4 gap-2 w-full rounded-lg border border-border/40 bg-muted/70 p-1">
                  <TabsTrigger
                    value="overview"
                    className="rounded-md px-3 py-2 text-xs font-semibold tracking-wide transition-colors data-[state=active]:bg-primary data-[state=active]:text-primary-foreground data-[state=active]:shadow-sm data-[state=inactive]:bg-transparent data-[state=inactive]:text-muted-foreground"
                  >
                    Overview
                  </TabsTrigger>
                  <TabsTrigger
                    value="documents"
                    className="rounded-md px-3 py-2 text-xs font-semibold tracking-wide transition-colors data-[state=active]:bg-primary data-[state=active]:text-primary-foreground data-[state=active]:shadow-sm data-[state=inactive]:bg-transparent data-[state=inactive]:text-muted-foreground"
                  >
                    Documents
                  </TabsTrigger>
                  <TabsTrigger
                    value="history"
                    className="rounded-md px-3 py-2 text-xs font-semibold tracking-wide transition-colors data-[state=active]:bg-primary data-[state=active]:text-primary-foreground data-[state=active]:shadow-sm data-[state=inactive]:bg-transparent data-[state=inactive]:text-muted-foreground"
                  >
                    History
                  </TabsTrigger>
                  <TabsTrigger
                    value="chat"
                    className="rounded-md px-3 py-2 text-xs font-semibold tracking-wide transition-colors data-[state=active]:bg-primary data-[state=active]:text-primary-foreground data-[state=active]:shadow-sm data-[state=inactive]:bg-transparent data-[state=inactive]:text-muted-foreground"
                  >
                    Chat
                  </TabsTrigger>
                </TabsList>
              </div>
              <div className="flex-1 min-h-0">
                <TabsContent value="overview" className="flex flex-1 min-h-0">
                  <ScrollArea className="flex-1 min-h-0 px-4 py-4">
                    {active && (
                      <div className="flex flex-col gap-4">
                        <SectionBlock title="Customer" cardClassName="p-0">
                          <Link
                            to="/dashboard/customers"
                            className="flex w-full items-center gap-3 px-3 py-3 group hover:bg-muted/40 rounded-md transition-colors"
                            aria-label="Open customer profile"
                          >
                            <Avatar className="h-10 w-10">
                              {active.customer.avatar && <AvatarImage src={active.customer.avatar} />}
                              <AvatarFallback>{active.customer.name.slice(0, 2).toUpperCase()}</AvatarFallback>
                            </Avatar>
                            <div className="space-y-1">
                              <div className="text-sm font-semibold">{active.customer.name}</div>
                              <div className="text-xs text-muted-foreground">{maskEmail(active.customer.email)}</div>
                            </div>
                            <ChevronRight className="ml-auto w-4 h-4 text-muted-foreground transition-transform group-hover:translate-x-0.5 group-hover:text-foreground" />
                          </Link>
                        </SectionBlock>

                        <div className="flex flex-col gap-3">
                          {[
                            { title: "AI Diagnoses", lines: diagnoses, empty: "No diagnoses available." },
                            { title: "AI Actions Taken", lines: aiActions, empty: "No AI actions recorded." },
                            { title: "Suggested Actions", lines: suggestedActions, empty: "No pending actions." },
                          ].map((section) => (
                            <SectionBlock key={section.title} title={section.title} cardClassName="space-y-2">
                              {section.lines.length > 0 ? (
                                section.lines.map((line, lineIdx) => (
                                  <div key={`${section.title}-${lineIdx}`} className="text-sm leading-snug text-foreground">
                                    {line}
                                  </div>
                                ))
                              ) : (
                                <div className="text-sm text-muted-foreground">{section.empty}</div>
                              )}
                            </SectionBlock>
                          ))}
                        </div>
                      </div>
                    )}
                  </ScrollArea>
                </TabsContent>

                <TabsContent value="documents" className="flex flex-1 min-h-0">
                  <ScrollArea className="flex-1 min-h-0 px-4 py-4">
                    {active ? (
                      <div className="flex flex-col gap-4">
                        <SectionBlock title="Documents" cardClassName="space-y-3">
                          {(demoDocumentsByType[caseType] || []).length === 0 ? (
                            <div className="text-sm text-muted-foreground">No documents attached.</div>
                          ) : (
                            demoDocumentsByType[caseType].map((f, idx) => {
                              const isLast = idx === demoDocumentsByType[caseType].length - 1;
                              return (
                                <React.Fragment key={`${f.name}-${idx}`}>
                                  <div className="flex items-center gap-3">
                                    <div className="flex h-9 w-9 items-center justify-center rounded-md bg-muted">
                                      <FileText className="w-4 h-4 text-muted-foreground" />
                                    </div>
                                    <div className="flex-1 min-w-0">
                                      <div className="text-sm font-semibold leading-snug">{f.name}</div>
                                      <div className="text-xs text-muted-foreground leading-tight">{f.type} • {f.size}</div>
                                    </div>
                                    <div className="flex items-center gap-2">
                                      <Button variant="ghost" size="sm">Open</Button>
                                      <Button variant="secondary" size="sm">Download</Button>
                                    </div>
                                  </div>
                                  {!isLast && <Separator className="bg-border/60" />}
                                </React.Fragment>
                              );
                            })
                          )}
                        </SectionBlock>
                      </div>
                    ) : (
                      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">Select a case to view documents.</div>
                    )}
                  </ScrollArea>
                </TabsContent>

                <TabsContent value="history" className="flex flex-1 min-h-0">
                  <ScrollArea className="flex-1 min-h-0 px-4 py-4">
                    {active ? (
                      <div className="flex flex-col gap-4">
                        <SectionBlock title="Case Updates" cardClassName="space-y-3">
                          {historyItems.length === 0 ? (
                            <div className="text-sm text-muted-foreground">No history entries.</div>
                          ) : (
                            historyItems.map((item, idx) => {
                              const isLast = idx === historyItems.length - 1;
                              return (
                                <React.Fragment key={item.id}>
                                  <div className="space-y-1.5">
                                    <div className="text-[11px] text-muted-foreground leading-tight">{item.date}</div>
                                    <div className="text-sm text-foreground leading-snug">{item.text}</div>
                                  </div>
                                  {!isLast && <Separator className="bg-border/60" />}
                                </React.Fragment>
                              );
                            })
                          )}
                        </SectionBlock>
                      </div>
                    ) : (
                      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">Select a case to view history.</div>
                    )}
                  </ScrollArea>
                </TabsContent>

                <TabsContent value="chat" className="flex flex-1 min-h-0">
                  <ScrollArea className="flex-1 min-h-0 px-4 py-4">
                    {active ? (
                      <div className="flex flex-col gap-4">
                        <SectionBlock title="Chat Transcript" cardClassName="space-y-3">
                          <div className="space-y-3">
                            <div className="flex items-start gap-2">
                              <Avatar className="h-7 w-7"><AvatarFallback>CU</AvatarFallback></Avatar>
                              <div className="rounded-lg border border-border/60 px-3 py-2 max-w-[75%]">
                                <div className="text-sm leading-snug">Hi, I was charged twice for last month.</div>
                              </div>
                            </div>
                            <div className="flex items-start gap-2 justify-end">
                              <div className="rounded-lg border border-border/60 px-3 py-2 max-w-[75%] bg-primary/10">
                                <div className="text-sm leading-snug">Sorry about that! Let me check your invoice.</div>
                              </div>
                              <Avatar className="h-7 w-7"><AvatarFallback>AG</AvatarFallback></Avatar>
                            </div>
                          </div>
                        </SectionBlock>
                      </div>
                    ) : (
                      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">Select a case to view chat.</div>
                    )}
                  </ScrollArea>
                </TabsContent>
              </div>
            </Tabs>

            <div className="px-4 py-3 border-t border-border/60 space-y-3">
              <div className="flex flex-col gap-2 sm:flex-row">
                <Button
                  className={cn(
                    'flex-1 justify-center gap-2 rounded-lg border border-border/70 bg-muted/70 text-sm font-medium text-card-foreground shadow-none transition',
                    'hover:bg-muted/60 hover:border-border/60 hover:text-card-foreground hover:shadow-sm'
                  )}
                >
                  <User className="h-4 w-4 text-muted-foreground" />
                  <span>Assign</span>
                </Button>
                <Button
                  className={cn(
                    'flex-1 justify-center gap-2 rounded-lg text-sm font-semibold tracking-wide',
                    'bg-primary text-primary-foreground shadow-sm hover:bg-primary/90'
                  )}
                >
                  <CheckCircle2 className="h-4 w-4" />
                  <span>Resolve</span>
                </Button>
              </div>
            </div>
          </div>
        </SheetContent>
      </Sheet>

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
