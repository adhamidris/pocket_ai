import React, { useEffect } from "react";
import type { DateRange } from "react-day-picker";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Checkbox } from "@/components/ui/checkbox";
import { Card } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Drawer, DrawerContent, DrawerHeader, DrawerTitle, DrawerFooter, DrawerTrigger, DrawerClose } from "@/components/ui/drawer";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Calendar } from "@/components/ui/calendar";
import { Slider } from "@/components/ui/slider";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { DropdownMenu, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuContent, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Pagination, PaginationContent, PaginationItem, PaginationNext, PaginationPrevious, PaginationLink } from "@/components/ui/pagination";
import {
  Search,
  ChevronDown,
  EllipsisVertical,
  Mail,
  MessageCircle,
  Phone,
  Filter,
  UserPlus,
  Edit,
  User,
  MapPin,
  Activity,
  Tag,
  AlertTriangle,
  Bot,
  CheckCircle2,
  Clock,
  Timer,
  Flame,
  Smile,
  FileText,
  Download,
  ClipboardList,
  Plus,
} from "lucide-react";
import { cn } from "@/lib/utils";

type Channel = "email" | "chat" | "whatsapp" | "phone";
type Status = "Active" | "Inactive";
type Customer = {
  id: string;
  name: string;
  email: string;
  avatar?: string;
  channel: Channel;
  status: Status;
  phone?: string;
  address?: string;
  conversations: number;
  satisfaction: number; // 0-100
  lastContact: string; // ISO
  totalValue: number; // USD
  owner: string;
  vip?: boolean;
  tags: string[];
  joinedAt: string; // ISO
};

const Sidebar = () => {
  const items = [
    { label: "Home", to: "/dashboard" },
    { label: "Cases", to: "/dashboard/cases" },
    { label: "Customers", to: "/dashboard/customers", active: true },
    { label: "Agents", to: "/dashboard/agents" },
  ];
  return (
    <aside className="hidden md:flex w-56 shrink-0 border-r border-border/70 bg-card/60 backdrop-blur-sm min-h-screen sticky top-0">
      <div className="flex flex-col w-full p-3 gap-2">
        <div className="px-2 py-3 text-lg font-semibold">Dashboard <span className="text-xs align-top text-muted-foreground">v0.1</span></div>
        <nav className="mt-1 flex-1 space-y-1">
          {items.map(it => (
            <Link key={it.label} to={it.to} className={`w-full block text-left px-3 py-2 rounded-md text-sm flex items-center justify-between ${it.active ? 'bg-primary/10 text-primary' : 'hover:bg-muted/60'}`}>
              <span>{it.label}</span>
            </Link>
          ))}
        </nav>
        <div className="mt-auto rounded-xl p-3 bg-gradient-to-br from-primary/20 via-primary/10 to-primary/5">
          <div className="text-sm font-semibold">Upgrade to PRO</div>
          <div className="text-xs text-muted-foreground mb-2">Get access to all features</div>
          <Button size="sm" className="w-full bg-gradient-primary text-white hover:opacity-90">Get Pro Now!</Button>
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

const seed: Customer[] = [
  {
    id: "CU-201",
    name: "Sarah Johnson",
    email: "sarah.johnson@gmail.com",
    avatar: "https://api.dicebear.com/7.x/avataaars/svg?seed=Sarah",
    channel: "email",
    status: "Active",
    phone: "+1 (415) 555-2109",
    address: "341 Market St, San Francisco, CA",
    conversations: 8,
    satisfaction: 92,
    lastContact: new Date(Date.now() - 1000 * 60 * 45).toISOString(),
    totalValue: 12400,
    owner: "Nancy AI",
    vip: true,
    tags: ["VIP", "Enterprise"],
    joinedAt: new Date(Date.now() - 1000 * 60 * 60 * 24 * 120).toISOString(),
  },
  {
    id: "CU-198",
    name: "Michael Chen",
    email: "michael.chen@acme.com",
    channel: "chat",
    status: "Active",
    phone: "+1 (312) 555-8891",
    address: "88 Orchard Ave, Chicago, IL",
    conversations: 5,
    satisfaction: 88,
    lastContact: new Date(Date.now() - 1000 * 60 * 110).toISOString(),
    totalValue: 9400,
    owner: "Alex Rivera",
    tags: ["Trial"],
    joinedAt: new Date(Date.now() - 1000 * 60 * 60 * 24 * 45).toISOString(),
  },
  {
    id: "CU-190",
    name: "Emma Rodriguez",
    email: "emma.rodriguez@outlook.com",
    channel: "phone",
    status: "Inactive",
    phone: "+44 20 7946 1200",
    conversations: 2,
    satisfaction: 74,
    lastContact: new Date(Date.now() - 1000 * 60 * 60 * 26).toISOString(),
    totalValue: 1200,
    owner: "—",
    tags: ["Churn-risk"],
    joinedAt: new Date(Date.now() - 1000 * 60 * 60 * 24 * 260).toISOString(),
  },
  {
    id: "CU-176",
    name: "David Kim",
    email: "d.kim@globex.com",
    channel: "email",
    status: "Active",
    phone: "+1 (646) 555-3012",
    address: "117 Madison Ave, New York, NY",
    conversations: 11,
    satisfaction: 95,
    lastContact: new Date(Date.now() - 1000 * 60 * 60 * 2).toISOString(),
    totalValue: 18800,
    owner: "Nancy AI",
    vip: true,
    tags: ["VIP"],
    joinedAt: new Date(Date.now() - 1000 * 60 * 60 * 24 * 365).toISOString(),
  },
];

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

const maskEmail = (email: string) => email;

const useDebounced = (val: string, delay = 300) => {
  const [d, setD] = React.useState(val);
  React.useEffect(() => { const id = setTimeout(() => setD(val), delay); return () => clearTimeout(id); }, [val, delay]);
  return d;
};

type CaseType = 'billing' | 'request' | 'bug' | 'inquiry' | 'complaint' | 'feedback' | 'lead';
type CasePriority = 'low' | 'medium' | 'high';
type CaseStatusFilter = 'needs' | 'resolved';
type CaseTone = 'positive' | 'neutral' | 'negative' | 'frustrated';

type CaseItem = {
  id: string;
  title: string;
  description: string;
  type: CaseType;
  tone: CaseTone;
  status: CaseStatusFilter;
  priority: CasePriority;
  requiredActions: string[];
  aiActions: string[];
  createdAt: string;
  updatedAt?: string;
};

type CaseTimelineEntry = {
  id: string;
  date: string;
  channel: 'email' | 'whatsapp' | 'web';
  text: string;
};

type CaseDocument = { name: string };

type CaseMessage = {
  id: string;
  text: string;
  isBot: boolean;
  timestamp: string;
  status?: 'sending' | 'sent' | 'delivered' | 'read';
};

type ActivityEntry = {
  id: string;
  title: string;
  description: string;
  timestamp: string;
  agent: string;
};

const CASE_DATA: CaseItem[] = [
  {
    id: 'C-1024',
    title: 'Double charge',
    description: 'Customer reports being charged twice on the same invoice and requests a refund.',
    type: 'billing',
    tone: 'frustrated',
    status: 'needs',
    priority: 'high',
    requiredActions: [
      'Verify last two invoices',
      'Issue refund for duplicate charge',
    ],
    aiActions: [
      'Collected invoice IDs from user',
      'Summarized billing history to the customer',
    ],
    createdAt: '2024-01-15T10:36:00Z',
  },
  {
    id: 'C-1025',
    title: 'Export analytics',
    description: 'User wants an export option to share analytics with their team on a weekly basis.',
    type: 'request',
    tone: 'neutral',
    status: 'needs',
    priority: 'medium',
    requiredActions: ['Create product ticket and tag as feature'],
    aiActions: ['Captured user use-case and frequency'],
    createdAt: '2024-01-14T14:10:00Z',
  },
  {
    id: 'C-1026',
    title: 'Password reset failing',
    description: 'Reset emails are not reaching the account owner, blocking access to the product.',
    type: 'bug',
    tone: 'negative',
    status: 'needs',
    priority: 'high',
    requiredActions: [
      'Escalate to auth team',
      'Verify reset email delivery & logs',
    ],
    aiActions: [
      'Guided user through reset flow',
      'Checked rate limits and recent errors',
    ],
    createdAt: '2024-01-15T08:05:00Z',
  },
  {
    id: 'C-1027',
    title: 'Upgrade plan inquiry',
    description: 'Customer evaluates plan tiers and needs guidance on the best fit for usage.',
    type: 'inquiry',
    tone: 'neutral',
    status: 'needs',
    priority: 'low',
    requiredActions: ['Share pricing tiers and limits'],
    aiActions: ['Summarized Pro vs. Enterprise features'],
    createdAt: '2024-01-13T11:22:00Z',
  },
  {
    id: 'C-1028',
    title: 'Slack integration request',
    description: 'Customer asks for early access to the Slack integration to streamline alerts.',
    type: 'request',
    tone: 'positive',
    status: 'needs',
    priority: 'medium',
    requiredActions: ['Provide early-access steps'],
    aiActions: ['Collected workspace URL and scope needs'],
    createdAt: '2024-01-13T09:40:00Z',
  },
  {
    id: 'C-1019',
    title: 'Webhook timeout',
    description: 'Intermittent webhook timeouts are causing delays in downstream system updates.',
    type: 'bug',
    tone: 'positive',
    status: 'resolved',
    priority: 'low',
    requiredActions: [],
    aiActions: [
      'Restarted integration connector via MCP',
      'Replayed failed events (last 50)',
    ],
    createdAt: '2024-01-12T09:20:00Z',
    updatedAt: '2024-01-12T10:05:00Z',
  },
  {
    id: 'C-1018',
    title: 'Billing address update',
    description: 'Need to adjust billing address on file and confirm the change with finance.',
    type: 'billing',
    tone: 'neutral',
    status: 'resolved',
    priority: 'low',
    requiredActions: ['Confirm address change with finance'],
    aiActions: ['Validated new address format'],
    createdAt: '2024-01-10T15:00:00Z',
    updatedAt: '2024-01-10T16:12:00Z',
  },
  {
    id: 'C-1017',
    title: 'Feature feedback on dashboard',
    description: 'Positive feedback on dashboard but highlights UI tweaks to improve clarity.',
    type: 'feedback',
    tone: 'positive',
    status: 'resolved',
    priority: 'medium',
    requiredActions: [],
    aiActions: ['Summarized feedback and tagged product'],
    createdAt: '2024-01-09T13:35:00Z',
    updatedAt: '2024-01-09T14:10:00Z',
  },
  {
    id: 'C-1029',
    title: 'Response delay complaint',
    description: 'Customer dissatisfaction due to perceived delays in support response times.',
    type: 'complaint',
    tone: 'negative',
    status: 'needs',
    priority: 'medium',
    requiredActions: ['Apologize and share SLA timeline'],
    aiActions: ['Analyzed queue wait times'],
    createdAt: '2024-01-15T07:55:00Z',
  },
];

const CASE_TIMELINE: CaseTimelineEntry[] = [
  { id: 'u1', date: 'Jan 15, 10:32 AM', channel: 'whatsapp', text: 'Customer requested status on duplicate charge and expressed dissatisfaction; escalation requested.' },
  { id: 'u2', date: 'Jan 15, 11:05 AM', channel: 'email', text: 'Refund timeline communicated (3–5 business days); case escalated to billing for expedited review.' },
  { id: 'u3', date: 'Jan 16, 09:02 AM', channel: 'web', text: 'Customer requested daily status updates pending refund confirmation.' },
  { id: 'u4', date: 'Jan 17, 08:45 AM', channel: 'email', text: 'Refund initiated; transaction reference provided; advised to verify statement within 24–48 hours.' },
];

const CASE_DOCUMENTS: CaseDocument[] = [
  { name: 'Invoice_2024-01-13_1025.pdf' },
  { name: 'Chat_Transcript_2024-01-15.txt' },
  { name: 'Screenshot_Account_Settings.png' },
];

const CASE_MESSAGES: CaseMessage[] = [
  { id: 'm1', text: 'Hi, I was double charged on my latest invoice.', isBot: false, timestamp: '2024-01-15T10:32:00Z', status: 'read' },
  { id: 'm2', text: 'I can help with that. Could you share the invoice IDs?', isBot: true, timestamp: '2024-01-15T10:33:00Z' },
  { id: 'm3', text: 'INV-10021 and INV-10022.', isBot: false, timestamp: '2024-01-15T10:34:00Z', status: 'delivered' },
];

const CUSTOMER_ACTIVITY: ActivityEntry[] = [
  {
    id: 'a1',
    title: 'Started chat conversation',
    description: 'Customer initiated support chat about billing inquiry',
    timestamp: '2 hours ago',
    agent: 'Nancy',
  },
  {
    id: 'a2',
    title: 'Added note',
    description: 'Customer prefers email communication over phone calls',
    timestamp: '1 day ago',
    agent: 'You',
  },
  {
    id: 'a3',
    title: 'Resolved technical issue',
    description: 'Successfully helped customer with account access problem',
    timestamp: '3 days ago',
    agent: 'Jack',
  },
];

const CASE_SUMMARY_MAP: Record<CaseType, string[]> = {
  billing: [
    'Duplicate transaction on the latest invoice.',
    'Statement total reflects the unintended repeat charge.',
  ],
  request: [
    'CSV export for analytics requested.',
    'Needed to share reports with stakeholders.',
  ],
  bug: [
    'Integration webhook experienced timeouts.',
    'Event delivery intermittently failed in the affected window.',
  ],
  complaint: [
    'Service outcome did not meet expectations.',
    'Dissatisfaction reported with the recent experience.',
  ],
  inquiry: [
    'Clarification requested on a product capability.',
    'Information needed before proceeding.',
  ],
  feedback: [
    'Constructive feedback shared on product/support.',
    'Captured for future improvement planning.',
  ],
  lead: [
    'Prospective customer expressed interest.',
    'Assessing solution fit for their use case.',
  ],
};

const formatCaseSummary = (item: CaseItem) => CASE_SUMMARY_MAP[item.type]?.join('\n') ?? '';

const formatCurrencyCompact = (value: number) => {
  if (Math.abs(value) >= 1000) {
    return `$${(value / 1000).toFixed(1)}k`;
  }
  return `$${value}`;
};

const formatCustomerId = (id: string) => {
  if (!id) return '—';
  const numeric = id.replace(/\D/g, '');
  return `CUST-${numeric.padStart(4, '0')}`;
};

const formatShortDate = (iso: string) => {
  const date = new Date(iso);
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
};

const formatTime = (iso: string) => {
  const date = new Date(iso);
  return date.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
};

const SectionBlock = ({
  title,
  children,
  icon: Icon,
  action,
  cardClassName,
  headingClassName,
}: {
  title: string;
  children: React.ReactNode;
  icon?: React.ComponentType<{ className?: string }>;
  action?: React.ReactNode;
  cardClassName?: string;
  headingClassName?: string;
}) => (
  <div className="space-y-2">
    <div className={cn('flex items-center justify-between text-[11px] font-semibold uppercase tracking-wide text-muted-foreground', headingClassName)}>
      <div className="flex items-center gap-2">
        {Icon && <Icon className="h-3.5 w-3.5" />}
        <span>{title}</span>
      </div>
      {action ? <div className="flex items-center gap-2">{action}</div> : null}
    </div>
    <Card className={cn('border border-border/60 bg-muted/15 px-3 py-3', cardClassName)}>{children}</Card>
  </div>
);

const getPriorityMeta = (priority: CasePriority) => {
  switch (priority) {
    case 'low':
      return { Icon: Clock, label: 'Low', color: 'text-muted-foreground', bg: 'bg-muted/70' };
    case 'medium':
      return { Icon: Timer, label: 'Medium', color: 'text-amber-500', bg: 'bg-amber-500/10' };
    case 'high':
      return { Icon: Flame, label: 'High', color: 'text-rose-500', bg: 'bg-rose-500/10' };
    default:
      return { Icon: Clock, label: 'Low', color: 'text-muted-foreground', bg: 'bg-muted/70' };
  }
};

const getCaseTypeMeta = (type: CaseType) => {
  switch (type) {
    case 'billing':
      return { label: 'Billing', className: 'border border-border/60 bg-muted/50 text-muted-foreground/90' };
    case 'bug':
      return { label: 'Bug', className: 'border border-border/60 bg-muted/50 text-muted-foreground/90' };
    case 'complaint':
      return { label: 'Complaint', className: 'border border-border/60 bg-muted/50 text-muted-foreground/90' };
    case 'request':
      return { label: 'Request', className: 'border border-border/60 bg-muted/50 text-muted-foreground/90' };
    case 'inquiry':
      return { label: 'Inquiry', className: 'border border-border/60 bg-muted/50 text-muted-foreground/90' };
    case 'feedback':
      return { label: 'Feedback', className: 'border border-border/60 bg-muted/50 text-muted-foreground/90' };
    case 'lead':
      return { label: 'Lead', className: 'border border-border/60 bg-muted/50 text-muted-foreground/90' };
    default:
      return { label: type.charAt(0).toUpperCase() + type.slice(1), className: 'border border-border/60 bg-muted/50 text-muted-foreground/90' };
  }
};

const getChannelMeta = (channel: CaseTimelineEntry['channel']) => {
  switch (channel) {
    case 'email':
      return { label: 'EM', className: 'bg-primary/15 text-primary' };
    case 'whatsapp':
      return { label: 'WA', className: 'bg-emerald-500/15 text-emerald-500' };
    case 'web':
      return { label: 'WEB', className: 'bg-sky-500/15 text-sky-500' };
    default:
      return { label: channel.toUpperCase(), className: 'bg-muted text-muted-foreground' };
  }
};

type CustomerTab = 'overview' | 'cases' | 'activity' | 'notes';

const Customers = () => {
  const [items, setItems] = React.useState<Customer[]>(seed);
  const [panelOpen, setPanelOpen] = React.useState(false);
  const [active, setActive] = React.useState<Customer | null>(null);
  const [tab, setTab] = React.useState<CustomerTab>('overview');
  const [query, setQuery] = React.useState("");
  const q = useDebounced(query, 300);
  const [status, setStatus] = React.useState<Status | "All">("All");
  const [channels, setChannels] = React.useState<Set<Channel>>(new Set());
  const [tags, setTags] = React.useState<Set<string>>(new Set());
  const [openFilters, setOpenFilters] = React.useState(false);
  const [joinedRange, setJoinedRange] = React.useState<DateRange | undefined>(undefined);
  const [valueRange, setValueRange] = React.useState<[number, number]>([0, 20000]);
  const [selected, setSelected] = React.useState<Set<string>>(new Set());
  const [density, setDensity] = React.useState<"compact" | "comfortable">("compact");
  const rowH = density === "compact" ? "h-11" : "h-13";
  const [pageSize, setPageSize] = React.useState<number>(() => Number(localStorage.getItem("customers.pageSize") || 25));
  const [page, setPage] = React.useState(1);
  const [isNarrow, setIsNarrow] = React.useState(false);
  const [viewportWidth, setViewportWidth] = React.useState(() => (typeof window !== 'undefined' ? window.innerWidth : 0));
  const panelWidth = React.useMemo(() => {
    if (isNarrow || !viewportWidth) return undefined;
    const calculated = Math.min(Math.max(viewportWidth * 0.58, 560), 920);
    return `${Math.round(calculated)}px`;
  }, [isNarrow, viewportWidth]);
  const [caseFilter, setCaseFilter] = React.useState<CaseStatusFilter>('needs');
  const [selectedCase, setSelectedCase] = React.useState<CaseItem | null>(null);
  const [caseDetailsTab, setCaseDetailsTab] = React.useState<'overview' | 'documents' | 'history' | 'chat'>('overview');
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 1023px)");
    const onChange = () => setIsNarrow(mq.matches);
    mq.addEventListener("change", onChange);
    setIsNarrow(mq.matches);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const onResize = () => setViewportWidth(window.innerWidth);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  React.useEffect(() => {
    setTab('overview');
  }, [active?.id]);
  React.useEffect(() => {
    setCaseFilter('needs');
    setSelectedCase(null);
    setCaseDetailsTab('overview');
  }, [active?.id]);
  React.useEffect(() => {
    if (tab !== 'cases' && selectedCase) {
      setSelectedCase(null);
    }
  }, [tab, selectedCase]);
  React.useEffect(() => {
    setCaseDetailsTab('overview');
  }, [selectedCase?.id]);

  const toggleTag = (t: string) => setTags((prev) => {
    const next = new Set(prev);
    if (next.has(t)) {
      next.delete(t);
    } else {
      next.add(t);
    }
    return next;
  });
  const toggleChannel = (c: Channel) => setChannels((prev) => {
    const next = new Set(prev);
    if (next.has(c)) {
      next.delete(c);
    } else {
      next.add(c);
    }
    return next;
  });

  const filtered = React.useMemo(() => {
    let arr = [...items];
    if (status !== "All") arr = arr.filter((i) => i.status === status);
    if (channels.size) arr = arr.filter((i) => channels.has(i.channel));
    if (tags.size) arr = arr.filter((i) => i.tags.some((t) => tags.has(t)));
    if (q) {
      const needle = q.toLowerCase();
      arr = arr.filter((i) => i.name.toLowerCase().includes(needle) || i.email.toLowerCase().includes(needle) || i.tags.join(" ").toLowerCase().includes(needle));
    }
    if (joinedRange?.from && joinedRange?.to) {
      arr = arr.filter((i) => {
        const ts = new Date(i.joinedAt).getTime();
        return ts >= joinedRange.from!.getTime() && ts <= joinedRange.to!.getTime();
      });
    }
    arr = arr.filter((i) => i.totalValue >= valueRange[0] && i.totalValue <= valueRange[1]);
    // default sort by last contact desc
    arr.sort((a, b) => new Date(b.lastContact).getTime() - new Date(a.lastContact).getTime());
    return arr;
  }, [items, status, channels, tags, q, joinedRange, valueRange]);

  useEffect(() => setPage(1), [status, channels, tags, q, joinedRange, valueRange, pageSize]);
  const total = filtered.length;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const pageItems = filtered.slice((page - 1) * pageSize, page * pageSize);
  const allSelectedOnPage = pageItems.length > 0 && pageItems.every((i) => selected.has(i.id));
  const someSelectedOnPage = pageItems.some((i) => selected.has(i.id)) && !allSelectedOnPage;
  const filteredCases = React.useMemo(() => CASE_DATA.filter((c) => c.status === caseFilter), [caseFilter]);
  const caseMessages = React.useMemo(() => CASE_MESSAGES, []);
  const formattedCustomerId = React.useMemo(() => (active ? formatCustomerId(active.id) : '—'), [active]);
  const joinedLabel = React.useMemo(() => (active?.joinedAt ? new Date(active.joinedAt).toLocaleDateString() : null), [active]);
  const contactRows = React.useMemo(() => {
    if (!active) return [] as Array<{ key: string; Icon: React.ComponentType<{ className?: string }>; value: string }>;
    const rows: Array<{ key: string; Icon: React.ComponentType<{ className?: string }>; value: string }> = [
      { key: 'email', Icon: Mail, value: active.email },
    ];
    if (active.phone) rows.push({ key: 'phone', Icon: Phone, value: active.phone });
    if (active.address) rows.push({ key: 'address', Icon: MapPin, value: active.address });
    return rows;
  }, [active]);
  const selectedCasePriorityMeta = React.useMemo(() => (selectedCase ? getPriorityMeta(selectedCase.priority) : null), [selectedCase]);
  const selectedCaseTypeMeta = React.useMemo(() => (selectedCase ? getCaseTypeMeta(selectedCase.type) : null), [selectedCase]);
  const selectedCaseSummary = React.useMemo(() => (selectedCase ? formatCaseSummary(selectedCase).split('\n').filter(Boolean) : []), [selectedCase]);

  const toggleSelectAllOnPage = () => setSelected((prev) => {
    const next = new Set(prev);
    if (allSelectedOnPage) pageItems.forEach((i) => next.delete(i.id)); else pageItems.forEach((i) => next.add(i.id));
    return next;
  });

  const openPanel = (it: Customer) => { setActive(it); setPanelOpen(true); };
  return (
    <div className="min-h-screen bg-background flex">
      <Sidebar />
      <main className="flex-1 overflow-auto">
        <div className="w-full px-4 md:px-6 lg:px-8 py-6">
          <section>
            {/* Header */}
            <div className="mb-4 flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
              <div className="text-xl md:text-2xl font-semibold">Customers</div>
              <div className="flex items-center gap-2">
                <Dialog>
                  <DialogTrigger asChild>
                    <Button className="gap-2"><UserPlus className="w-4 h-4" /> New Customer</Button>
                  </DialogTrigger>
                  <DialogContent>
                    <DialogHeader>
                      <DialogTitle>New Customer</DialogTitle>
                    </DialogHeader>
                    <div className="space-y-2">
                      <div className="grid grid-cols-2 gap-2">
                        <Input placeholder="Name" id="nc-name" />
                        <Input placeholder="Email" id="nc-email" />
                      </div>
                      <Button onClick={() => {
                        const name = (document.getElementById('nc-name') as HTMLInputElement)?.value?.trim();
                        const email = (document.getElementById('nc-email') as HTMLInputElement)?.value?.trim();
                        if (!name || !email) return;
                        const now = new Date().toISOString();
                        setItems((prev) => [{ id: `CU-${Math.floor(Math.random()*900)+100}`, name, email, channel: 'email', status: 'Active', conversations: 0, satisfaction: 0, lastContact: now, totalValue: 0, owner: '—', tags: [], joinedAt: now }, ...prev]);
                        // close is automatic via outside click; minimal implementation
                      }}>Create</Button>
                    </div>
                  </DialogContent>
                </Dialog>
              </div>
            </div>

            {/* Toolbar */}
            <div className="sticky top-0 z-10 bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60 border-y border-border/60">
              <div className="py-2 flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
                <div className="flex items-center gap-2 flex-wrap">
                  {/* Status chips */}
                  <Button size="sm" variant={status === 'All' ? 'default' : 'outline'} onClick={() => setStatus('All')}>All</Button>
                  <Button size="sm" variant={status === 'Active' ? 'default' : 'outline'} onClick={() => setStatus('Active')}>Active</Button>
                  <Button size="sm" variant={status === 'Inactive' ? 'default' : 'outline'} onClick={() => setStatus('Inactive')}>Inactive</Button>

                  {/* Channel chips */}
                  <Button size="sm" variant={channels.has('email') ? 'default' : 'outline'} onClick={() => toggleChannel('email')}>Email</Button>
                  <Button size="sm" variant={channels.has('chat') ? 'default' : 'outline'} onClick={() => toggleChannel('chat')}>Web</Button>
                  <Button size="sm" variant={channels.has('whatsapp') ? 'default' : 'outline'} onClick={() => toggleChannel('whatsapp')}>WA</Button>
                  <Button size="sm" variant={channels.has('phone') ? 'default' : 'outline'} onClick={() => toggleChannel('phone')}>Phone</Button>

                  {/* Tag chips */}
                  <Button size="sm" variant={tags.has('VIP') ? 'default' : 'outline'} onClick={() => toggleTag('VIP')}>VIP</Button>
                  <Button size="sm" variant={tags.has('Enterprise') ? 'default' : 'outline'} onClick={() => toggleTag('Enterprise')}>Enterprise</Button>
                  <Button size="sm" variant={tags.has('Trial') ? 'default' : 'outline'} onClick={() => toggleTag('Trial')}>Trial</Button>
                </div>
                <div className="flex items-center gap-2 w-full md:w-auto">
                  {/* Search */}
                  <div className="relative w-full md:w-64">
                    <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                    <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search name, email, tags…" className="pl-9 bg-input border-0" />
                  </div>

                  {/* Filters drawer */}
                  <Drawer open={openFilters} onOpenChange={setOpenFilters}>
                    <DrawerTrigger asChild>
                      <Button variant="outline" size="sm" className="gap-2"><Filter className="w-4 h-4" /> Filters</Button>
                    </DrawerTrigger>
                    <DrawerContent>
                      <DrawerHeader>
                        <DrawerTitle>Advanced Filters</DrawerTitle>
                      </DrawerHeader>
                      <div className="p-4 grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div>
                          <div className="text-sm font-medium mb-2">Joined range</div>
                          <Calendar mode="range" selected={joinedRange} onSelect={setJoinedRange} numberOfMonths={2} />
                        </div>
                        <div>
                          <div className="text-sm font-medium mb-2">Value range</div>
                          <div className="text-xs text-muted-foreground mb-2">${valueRange[0].toLocaleString()} – ${valueRange[1].toLocaleString()}</div>
                          <Slider min={0} max={25000} step={100} value={valueRange} onValueChange={(value) => setValueRange([value[0] ?? 0, value[1] ?? 0])} />
                        </div>
                      </div>
                      <DrawerFooter>
                        <Button onClick={() => setOpenFilters(false)}>Apply</Button>
                        <DrawerClose asChild>
                          <Button variant="outline">Close</Button>
                        </DrawerClose>
                      </DrawerFooter>
                    </DrawerContent>
                  </Drawer>

                  {/* Bulk actions */}
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button size="sm" disabled={selected.size === 0} className="gap-2">
                        Bulk actions
                        <ChevronDown className="w-4 h-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem disabled={selected.size === 0}>Assign Owner…</DropdownMenuItem>
                      <DropdownMenuItem disabled={selected.size === 0}>Add Tag…</DropdownMenuItem>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem disabled={selected.size === 0}>Export CSV</DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
              </div>
            </div>

            {/* Table */}
            <div className="mt-3">
              <Card className="border border-border bg-card/60">
                <div className="relative overflow-x-auto overflow-y-auto max-h-[70vh]">
                  <Table>
                    <TableHeader className="sticky top-0 bg-card/90 backdrop-blur z-10">
                    <TableRow className="hover:bg-transparent">
                      <TableHead className="w-8">
                        <Checkbox checked={allSelectedOnPage} onCheckedChange={toggleSelectAllOnPage} aria-label="Select all" className={someSelectedOnPage ? "data-[state=indeterminate]:opacity-100" : ""} />
                      </TableHead>
                      <TableHead className="min-w-[220px]">Customer</TableHead>
                      <TableHead>Email</TableHead>
                      <TableHead>Conversations</TableHead>
                      <TableHead>Satisfaction</TableHead>
                      <TableHead>Last Contact</TableHead>
                      <TableHead className="w-8" />
                    </TableRow>
                    </TableHeader>
                    <TableBody>
                    {pageItems.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={7}>
                          <div className="p-6 text-sm text-muted-foreground flex items-center justify-between">
                            <div>No customers match your filters.</div>
                            {(status !== 'All' || channels.size || tags.size || q || (joinedRange.from && joinedRange.to) || valueRange[0] !== 0 || valueRange[1] !== 20000) && (
                              <Button variant="ghost" size="sm" onClick={() => { setStatus('All'); setChannels(new Set()); setTags(new Set()); setQuery(''); setJoinedRange(undefined); setValueRange([0,20000]); }}>
                                Clear all
                              </Button>
                            )}
                          </div>
                        </TableCell>
                      </TableRow>
                    ) : (
                      pageItems.map((c) => (
                        <TableRow key={c.id} className={`${rowH} cursor-pointer`} onClick={() => openPanel(c)}>
                          <TableCell className="w-8" onClick={(e) => e.stopPropagation()}>
                            <Checkbox
                              checked={selected.has(c.id)}
                              onCheckedChange={(value) =>
                                setSelected((prev) => {
                                  const next = new Set(prev);
                                  if (value) {
                                    next.add(c.id);
                                  } else {
                                    next.delete(c.id);
                                  }
                                  return next;
                                })
                              }
                            />
                          </TableCell>
                          <TableCell>
                            <div className="flex items-center gap-2">
                              <Avatar className="h-8 w-8">
                                {c.avatar && <AvatarImage src={c.avatar} />}
                                <AvatarFallback>{c.name.slice(0,2).toUpperCase()}</AvatarFallback>
                              </Avatar>
                              <div className="leading-tight">
                                <div className="text-sm font-medium flex items-center gap-2">
                                  <span>{c.name}</span>
                                </div>
                              </div>
                            </div>
                          </TableCell>
                          <TableCell className="text-sm">{maskEmail(c.email)}</TableCell>
                          <TableCell>{c.conversations}</TableCell>
                          <TableCell>{c.satisfaction}%</TableCell>
                          <TableCell>{formatRelative(c.lastContact)}</TableCell>
                          
                          <TableCell onClick={(e)=>e.stopPropagation()}>
                            <DropdownMenu>
                              <DropdownMenuTrigger asChild>
                                <Button variant="ghost" size="icon"><EllipsisVertical className="w-4 h-4" /></Button>
                              </DropdownMenuTrigger>
                              <DropdownMenuContent align="end">
                                <DropdownMenuItem onClick={() => openPanel(c)}>View</DropdownMenuItem>
                                <DropdownMenuItem onClick={() => window.open(`mailto:${c.email}`)}><Mail className="w-4 h-4 mr-2" /> Email</DropdownMenuItem>
                                <DropdownMenuSeparator />
                                <DropdownMenuItem>Create Case…</DropdownMenuItem>
                              </DropdownMenuContent>
                            </DropdownMenu>
                          </TableCell>
                        </TableRow>
                      ))
                    )}
                    </TableBody>
                  </Table>
                </div>
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
          </section>
        </div>
      </main>

      {/* Right Side Panel */}
      <Sheet open={panelOpen} onOpenChange={setPanelOpen}>
        <SheetContent
          side="right"
          className={`${isNarrow ? 'w-screen' : 'sm:max-w-none max-w-none'} p-0`}
          style={isNarrow ? undefined : panelWidth ? { width: panelWidth } : undefined}
        >
          <div className="h-full flex flex-col">
            <div className="px-4 py-3 border-b border-border/60 bg-card/80 backdrop-blur">
              <SheetHeader>
                <SheetTitle className="flex flex-col gap-3">
                  <div className="flex items-center gap-3">
                    <Avatar className="h-10 w-10">
                      {active?.avatar && <AvatarImage src={active.avatar} />}
                      <AvatarFallback>{active?.name ? active.name.slice(0, 2).toUpperCase() : 'CU'}</AvatarFallback>
                    </Avatar>
                    <div className="flex-1 flex flex-col gap-1">
                      <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide">{formattedCustomerId}</span>
                      <span className="text-lg font-semibold leading-none">{active?.name ?? 'Customer'}</span>
                    </div>
                  </div>
                </SheetTitle>
              </SheetHeader>
            </div>

            <Tabs value={tab} onValueChange={(value) => setTab(value as CustomerTab)} className="flex-1 flex flex-col overflow-hidden">
              {selectedCase === null && (
                <div className="px-4 pt-3">
                  <TabsList className="grid grid-cols-4 gap-2 w-full rounded-lg border border-border/40 bg-muted/70 p-1">
                    <TabsTrigger
                      value="overview"
                      className="rounded-md px-3 py-2 text-xs font-semibold tracking-wide transition-colors data-[state=active]:bg-primary data-[state=active]:text-primary-foreground data-[state=active]:shadow-sm data-[state=inactive]:bg-transparent data-[state=inactive]:text-muted-foreground"
                    >
                      Overview
                    </TabsTrigger>
                    <TabsTrigger
                      value="cases"
                      className="rounded-md px-3 py-2 text-xs font-semibold tracking-wide transition-colors data-[state=active]:bg-primary data-[state=active]:text-primary-foreground data-[state=active]:shadow-sm data-[state=inactive]:bg-transparent data-[state=inactive]:text-muted-foreground"
                    >
                      Cases
                    </TabsTrigger>
                    <TabsTrigger
                      value="activity"
                      className="rounded-md px-3 py-2 text-xs font-semibold tracking-wide transition-colors data-[state=active]:bg-primary data-[state=active]:text-primary-foreground data-[state=active]:shadow-sm data-[state=inactive]:bg-transparent data-[state=inactive]:text-muted-foreground"
                    >
                      Activity
                    </TabsTrigger>
                    <TabsTrigger
                      value="notes"
                      className="rounded-md px-3 py-2 text-xs font-semibold tracking-wide transition-colors data-[state=active]:bg-primary data-[state=active]:text-primary-foreground data-[state=active]:shadow-sm data-[state=inactive]:bg-transparent data-[state=inactive]:text-muted-foreground"
                    >
                      Notes
                    </TabsTrigger>
                  </TabsList>
                </div>
              )}
              <div className="flex-1 min-h-0 overflow-y-auto px-4 pb-4 space-y-4">
                <TabsContent value="overview">
                  <div className="space-y-4 pt-4">
                    <SectionBlock
                      title="Contact Information"
                      icon={User}
                      cardClassName="space-y-3 text-sm"
                    >
                      {!active ? (
                        <div className="text-xs text-muted-foreground">Select a customer to view details.</div>
                      ) : contactRows.length === 0 ? (
                        <div className="text-xs text-muted-foreground">No contact details available.</div>
                      ) : (
                        <div className="space-y-2">
                          {contactRows.map(({ key, Icon, value }) => (
                            <div key={key} className="flex items-center gap-3 text-card-foreground">
                              <Icon className="h-4 w-4 text-muted-foreground" />
                              <span className="break-words text-sm">{value}</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </SectionBlock>

                    <SectionBlock
                      title="Customer Stats"
                      icon={Activity}
                      cardClassName="grid grid-cols-2 gap-3 text-center"
                    >
                      <div className="space-y-1.5">
                        <div className="text-xl font-semibold text-card-foreground">{active?.conversations ?? '—'}</div>
                        <div className="text-xs leading-4 text-muted-foreground">Conversations</div>
                      </div>
                      <div className="space-y-1.5">
                        <div className="text-xl font-semibold text-card-foreground">{active ? `${active.satisfaction}%` : '—'}</div>
                        <div className="text-xs leading-4 text-muted-foreground">Satisfaction</div>
                      </div>
                    </SectionBlock>

                    <SectionBlock
                      title="Tags"
                      icon={Tag}
                      action={(
                        <Button variant="outline" size="icon" className="h-8 w-8" aria-label="Add tag">
                          <Plus className="h-4 w-4" />
                        </Button>
                      )}
                      cardClassName="space-y-3"
                    >
                      <div className="flex flex-wrap gap-2">
                        {active?.tags?.length ? (
                          active.tags.map((tag) => (
                            <Badge key={tag} variant="secondary" className="px-3 py-1 text-xs font-medium">
                              {tag}
                            </Badge>
                          ))
                        ) : (
                          <div className="text-xs text-muted-foreground">No tags yet.</div>
                        )}
                      </div>
                    </SectionBlock>
                  </div>
                </TabsContent>

                <TabsContent value="cases" className="flex h-full min-h-0 flex-col">
                  {selectedCase === null ? (
                    <Card className="p-0 flex-1 flex flex-col">
                      <div className="sticky top-0 z-10 border-b border-border/60 bg-card px-4 pt-4 pb-3">
                        <div className="grid grid-cols-2 gap-2 rounded-lg border border-border/40 bg-muted/70 p-1 text-xs font-semibold">
                          {([
                            { key: 'needs', label: 'Needs action' },
                            { key: 'resolved', label: 'Resolved' },
                          ] as const).map(({ key, label }) => (
                            <button
                              key={key}
                              type="button"
                              onClick={() => setCaseFilter(key)}
                              className={cn(
                                'flex items-center justify-center gap-2 rounded-md px-3 py-2 text-xs font-semibold tracking-wide transition-colors',
                                caseFilter === key ? 'bg-primary text-primary-foreground shadow-sm' : 'bg-transparent text-muted-foreground'
                              )}
                              aria-pressed={caseFilter === key}
                            >
                              {key === 'needs' ? <AlertTriangle className="h-4 w-4" /> : <CheckCircle2 className="h-4 w-4" />}
                              <span>{label}</span>
                            </button>
                          ))}
                        </div>
                      </div>
                      <div className="flex-1 px-4 pb-4 space-y-3">
                        {filteredCases.map((item, idx) => {
                          const priorityMeta = getPriorityMeta(item.priority);
                          const typeMeta = getCaseTypeMeta(item.type);
                          const isLast = idx === filteredCases.length - 1;
                          return (
                            <React.Fragment key={item.id}>
                              <button
                                type="button"
                                onClick={() => setSelectedCase(item)}
                                className="w-full rounded-lg border border-border/60 bg-card/80 px-4 py-3 text-left transition hover:border-primary/40 hover:bg-muted/50"
                              >
                                <div className="flex items-center justify-between text-[11px] font-medium text-muted-foreground">
                                  <span className="uppercase tracking-wide opacity-80">{item.id}</span>
                                  <span className="font-normal opacity-70">{formatShortDate(item.createdAt)}</span>
                                </div>
                                <div className="mt-2 flex flex-wrap items-start gap-3">
                                  <div className="flex-1 space-y-3">
                                    <div className="flex flex-wrap items-center gap-2">
                                      <div className="text-[15px] font-semibold leading-snug text-card-foreground">{item.title}</div>
                                      <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium tracking-wide ${typeMeta.className}`}>
                                        {typeMeta.label}
                                      </span>
                                    </div>
                                    <p className="text-sm leading-5 text-muted-foreground line-clamp-2">{item.description}</p>
                                  </div>
                                </div>
                              </button>
                              {!isLast && <Separator className="bg-border/60" />}
                            </React.Fragment>
                          );
                        })}
                        {filteredCases.length === 0 && (
                          <div className="rounded-lg border border-dashed border-border/70 p-6 text-center text-sm text-muted-foreground">
                            No cases in this bucket.
                          </div>
                        )}
                      </div>
                    </Card>
                  ) : (
                    <div className="space-y-4">
                      <Card className="p-4 space-y-3">
                        <div className="flex flex-wrap items-center justify-between gap-y-1 text-[11px] font-medium text-muted-foreground">
                          <span className="uppercase tracking-wide opacity-80">{selectedCase.id}</span>
                          <span className="font-normal opacity-70">{formatShortDate(selectedCase.createdAt)}</span>
                        </div>
                        <div className="flex flex-wrap items-center gap-2 mt-1">
                          <div className="text-lg font-semibold leading-tight text-card-foreground">{selectedCase.title}</div>
                          {selectedCaseTypeMeta && (
                            <span className="inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium tracking-wide border border-border/60 bg-muted/70 text-muted-foreground/80">
                              {selectedCaseTypeMeta.label}
                            </span>
                          )}
                        </div>
                        <div className="grid grid-cols-4 gap-2 rounded-lg border border-border/40 bg-muted/70 p-1 text-xs font-semibold tracking-wide mt-1.5">
                          {([
                            { key: 'overview', label: 'Overview' },
                            { key: 'documents', label: 'Documents' },
                            { key: 'history', label: 'History' },
                            { key: 'chat', label: 'Chat' },
                          ] as const).map(({ key, label }) => (
                            <button
                              key={key}
                              type="button"
                              onClick={() => setCaseDetailsTab(key)}
                              className={cn(
                                'rounded-md px-3 py-2 text-xs font-semibold tracking-wide transition-colors',
                                caseDetailsTab === key ? 'bg-primary text-primary-foreground shadow-sm' : 'bg-transparent text-muted-foreground'
                              )}
                            >
                              {label}
                            </button>
                          ))}
                        </div>
                      </Card>

                      {caseDetailsTab === 'overview' && (
                        <div className="space-y-4">
                          <SectionBlock
                            title="AI Diagnoses"
                            cardClassName="space-y-2 text-sm"
                          >
                            {selectedCaseSummary.map((line, idx) => (
                              <p key={idx} className="text-card-foreground/90">
                                {line}
                              </p>
                            ))}
                          </SectionBlock>

                          <SectionBlock
                            title="AI Actions Taken"
                            cardClassName="space-y-2 text-sm"
                          >
                            {selectedCase.aiActions.length ? (
                              selectedCase.aiActions.map((action, idx) => (
                                <p key={idx} className="text-card-foreground/90">
                                  {action}
                                </p>
                              ))
                            ) : (
                              <p className="text-muted-foreground">No AI actions recorded.</p>
                            )}
                          </SectionBlock>

                          <SectionBlock
                            title="Suggested Actions"
                            cardClassName="space-y-2 text-sm"
                          >
                            {selectedCase.requiredActions.length ? (
                              selectedCase.requiredActions.map((action, idx) => (
                                <p key={idx} className="text-card-foreground/90">
                                  {action}
                                </p>
                              ))
                            ) : (
                              <p className="text-muted-foreground">No pending actions.</p>
                            )}
                          </SectionBlock>
                        </div>
                      )}

                      {caseDetailsTab === 'documents' && (
                        <SectionBlock
                          title="DOCUMENTS"
                          cardClassName="space-y-3 text-sm"
                        >
                          {CASE_DOCUMENTS.length === 0 ? (
                            <div className="text-sm text-muted-foreground">No documents attached.</div>
                          ) : (
                            CASE_DOCUMENTS.map((doc, idx) => {
                              const isLast = idx === CASE_DOCUMENTS.length - 1;
                              return (
                                <React.Fragment key={doc.name}>
                                  <div className="flex items-center gap-3">
                                    <div className="flex h-9 w-9 items-center justify-center rounded-md bg-muted/70">
                                      <FileText className="h-4 w-4 text-muted-foreground" />
                                    </div>
                                    <div className="min-w-0 flex-1">
                                      <div className="text-sm font-semibold leading-snug text-card-foreground">{doc.name}</div>
                                      <div className="text-xs leading-tight text-muted-foreground/90">PDF • 250 KB</div>
                                    </div>
                                    <div className="flex items-center gap-2">
                                      <Button variant="ghost" size="sm" className="text-xs font-medium">Open</Button>
                                      <Button variant="secondary" size="sm" className="text-xs font-medium">Download</Button>
                                    </div>
                                  </div>
                                  {!isLast && <Separator className="bg-border/60" />}
                                </React.Fragment>
                              );
                            })
                          )}
                        </SectionBlock>
                      )}

                      {caseDetailsTab === 'history' && (
                        <SectionBlock
                          title="Case Updates"
                          cardClassName="space-y-3 text-sm"
                        >
                          {CASE_TIMELINE.length === 0 ? (
                            <div className="text-sm text-muted-foreground">No history entries.</div>
                          ) : (
                            CASE_TIMELINE.map((entry, idx) => {
                              const meta = getChannelMeta(entry.channel);
                              const isLast = idx === CASE_TIMELINE.length - 1;
                              return (
                                <React.Fragment key={entry.id}>
                                  <div className="space-y-1.5">
                                    <div className="flex items-center justify-between text-[11px] text-muted-foreground leading-tight">
                                      <span>{entry.date}</span>
                                      <span>{meta.label}</span>
                                    </div>
                                    <div className="text-sm leading-snug text-card-foreground">{entry.text}</div>
                                  </div>
                                  {!isLast && <Separator className="bg-border/60" />}
                                </React.Fragment>
                              );
                            })
                          )}
                        </SectionBlock>
                      )}

                      {caseDetailsTab === 'chat' && (
                        <SectionBlock
                          title="Chat Transcript"
                          icon={MessageCircle}
                          cardClassName="space-y-3 text-sm"
                        >
                          {caseMessages.map((message) => (
                            <div key={message.id} className={cn('flex flex-col gap-1', message.isBot ? 'items-start' : 'items-end')}>
                              <div className={cn(
                                'max-w-[75%] rounded-lg px-3 py-2 text-sm leading-relaxed',
                                message.isBot ? 'bg-muted text-foreground' : 'bg-primary text-primary-foreground'
                              )}>
                                {message.text}
                              </div>
                              <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
                                {message.isBot ? <Bot className="h-3 w-3 text-primary" /> : <User className="h-3 w-3" />}
                                <span>{formatTime(message.timestamp)}</span>
                                {!message.isBot && message.status && (
                                  <span className="inline-flex items-center gap-1 text-[10px]">
                                    <CheckCircle2 className="h-3 w-3" />
                                    <span className="capitalize">{message.status}</span>
                                  </span>
                                )}
                              </div>
                            </div>
                          ))}
                        </SectionBlock>
                      )}
                    </div>
                  )}
                </TabsContent>

                <TabsContent value="activity">
                  <div className="space-y-4 pt-4">
                    <SectionBlock
                      title="Recent Activity"
                      icon={Activity}
                      cardClassName="space-y-4 text-sm"
                    >
                      {CUSTOMER_ACTIVITY.map((item) => (
                        <div key={item.id} className="space-y-2 border-b border-border/60 pb-4 last:border-b-0 last:pb-0">
                          <div className="text-sm font-semibold text-card-foreground">{item.title}</div>
                          <p className="text-sm text-muted-foreground">{item.description}</p>
                          <div className="text-xs text-muted-foreground">{item.timestamp} • by {item.agent}</div>
                        </div>
                      ))}
                    </SectionBlock>
                  </div>
                </TabsContent>

                <TabsContent value="notes">
                  <div className="space-y-4 pt-4">
                    <SectionBlock
                      title="Notes"
                      icon={ClipboardList}
                      action={<Button variant="outline" size="sm">Add note</Button>}
                      cardClassName="space-y-3 text-sm"
                    >
                      <p className="text-sm italic text-muted-foreground text-center">
                        No notes yet. Add the first note to track important information about this customer.
                      </p>
                    </SectionBlock>
                  </div>
                </TabsContent>
              </div>
            </Tabs>

            <div className="px-4 py-3 border-t border-border/60 space-y-3">
              <div className="flex flex-col gap-2 sm:flex-row">
                <Button
                  variant="ghost"
                  className={cn(
                    'flex-1 justify-center gap-2 rounded-lg border border-border/40 bg-muted/30 text-sm font-medium text-card-foreground shadow-none',
                    'hover:bg-muted/50 hover:text-card-foreground'
                  )}
                >
                  <Mail className="h-4 w-4 text-muted-foreground" />
                  <span>Send email</span>
                </Button>
                <Button
                  variant="ghost"
                  className={cn(
                    'flex-1 justify-center gap-2 rounded-lg border border-border/40 bg-muted/30 text-sm font-medium text-card-foreground shadow-none',
                    'hover:bg-muted/50 hover:text-card-foreground'
                  )}
                >
                  <Phone className="h-4 w-4 text-muted-foreground" />
                  <span>Call customer</span>
                </Button>
              </div>
              {tab === 'cases' && selectedCase ? (
                <Button
                  className={cn(
                    'w-full justify-center rounded-lg text-sm font-semibold tracking-wide',
                    'bg-primary text-primary-foreground shadow-sm hover:bg-primary/90'
                  )}
                  onClick={() => setSelectedCase(null)}
                >
                  Back to cases
                </Button>
              ) : null}
            </div>
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
};

export default Customers;
