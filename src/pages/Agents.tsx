import React, { useEffect } from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
import { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator } from "@/components/ui/dropdown-menu";
import { Pagination, PaginationContent, PaginationItem, PaginationNext, PaginationPrevious, PaginationLink } from "@/components/ui/pagination";
import { Search, ChevronDown, EllipsisVertical, Bot, Rocket, Copy, Eye, EyeOff, Plus, X, Info } from "lucide-react";
import { cn } from "@/lib/utils";
import { AGENT_ROLES, AGENT_TONES, AGENT_TRAITS, ESCALATION_OPTIONS, type AgentRole, type AgentTone, type AgentTrait, type EscalationRule, type AgentStatus } from "@/lib/agentOptions";

type Agent = {
  id: string;
  name: string;
  roles: AgentRole[];
  status: AgentStatus;
  conversations: number;
  satisfaction: number; // 0-100
  aht: number; // seconds
  escalations: number;
  updatedAt: string; // ISO
  tone?: AgentTone;
  traits?: AgentTrait[];
  escalationRule?: EscalationRule;
  knowledge?: { mode: 'all' | 'select'; collections: string[] };
  tools?: { crm: boolean; knowledge: boolean; calendar: boolean; email: boolean };
  crmActions?: { createCase: boolean; updateFields: boolean; addNote: boolean };
  knowledgeSettings?: { topK: number; recencyBias: number; piiRedaction: boolean };
  routing?: { sentimentThreshold?: number; slaRiskMinutes?: number; workingHours?: string; fallbackAssignee?: string };
  kpis?: string[];
  resolvedCases?: number;
  openCases?: number;
  responseTimeSec?: number;
};

const COMMON_KPIS = [
  'Customer Satisfaction',
  'First Contact Resolution',
  'Response Time',
  'Resolution Rate',
  'Escalation Rate',
  'Sales Conversion',
  'Deflection Rate',
  'Average Handling Time',
]

const DEFAULT_KPI_SELECTION = ['Customer Satisfaction', 'First Contact Resolution']

const KNOWLEDGE_COLLECTIONS = [
  'Product KB',
  'Support SOPs',
  'Security Policies',
  'Pricing Guide',
  'Onboarding Handbook',
]

const Sidebar = () => {
  const items = [
    { label: "Home", to: "/dashboard" },
    { label: "Cases", to: "/dashboard/cases" },
    { label: "Leads", to: "/dashboard/leads" },
    { label: "Customers", to: "/dashboard/customers" },
    { label: "Agents", to: "/dashboard/agents", active: true },
    { label: "Knowledge", to: "/dashboard/knowledge" },
  ];

  return (
    <aside className="hidden md:flex w-56 shrink-0 border-r border-border/70 bg-card/60 backdrop-blur-sm min-h-screen sticky top-0">
      <div className="flex flex-col w-full p-3 gap-2">
        <div className="px-2 py-3 text-lg font-semibold">
          Dashboard <span className="text-xs align-top text-muted-foreground">v0.1</span>
        </div>
        <nav className="mt-1 flex-1 space-y-1">
          {items.map((item) => (
            <Link
              key={item.label}
              to={item.to}
              className={`w-full block text-left px-3 py-2 rounded-md text-sm flex items-center justify-between ${
                item.active ? "bg-primary/10 text-primary" : "hover:bg-muted/60"
              }`}
            >
              <span>{item.label}</span>
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

const seed: Agent[] = [
  {
    id: "AG-1201",
    name: "Nancy",
    roles: ["Support Agent"],
    status: "Active",
    conversations: 184,
    satisfaction: 92,
    aht: 68,
    escalations: 3,
    updatedAt: new Date(Date.now() - 1000 * 60 * 45).toISOString(),
    tone: "Professional",
    traits: ["Patient", "Detail-oriented"],
    escalationRule: "Negative sentiment",
    knowledge: { mode: 'select', collections: ['Product KB', 'Support SOPs'] },
    tools: { crm: true, knowledge: true, calendar: false, email: false },
    crmActions: { createCase: true, updateFields: true, addNote: true },
    knowledgeSettings: { topK: 4, recencyBias: 30, piiRedaction: true },
    routing: { sentimentThreshold: 30, slaRiskMinutes: 15, workingHours: 'Mon–Fri 9:00–17:00', fallbackAssignee: 'Tier 2' },
    resolvedCases: 168,
    openCases: 6,
    responseTimeSec: 72,
    kpis: [...DEFAULT_KPI_SELECTION, 'Escalation Rate'],
  },
  {
    id: "AG-1202",
    name: "Jack",
    roles: ["Billing Assistant"],
    status: "Inactive",
    conversations: 44,
    satisfaction: 88,
    aht: 75,
    escalations: 5,
    updatedAt: new Date(Date.now() - 1000 * 60 * 120).toISOString(),
    tone: "Concise",
    traits: ["Analytical"],
    escalationRule: "Always escalate complex",
    knowledge: { mode: 'all', collections: [] },
    tools: { crm: true, knowledge: true, calendar: false, email: true },
    crmActions: { createCase: true, updateFields: false, addNote: true },
    knowledgeSettings: { topK: 3, recencyBias: 0, piiRedaction: false },
    routing: { sentimentThreshold: 20, slaRiskMinutes: 20, workingHours: 'Mon–Sat 10:00–18:00', fallbackAssignee: 'Finance Lead' },
    resolvedCases: 36,
    openCases: 8,
    responseTimeSec: 95,
    kpis: ['Customer Satisfaction', 'Average Handling Time'],
  },
  {
    id: "AG-1203",
    name: "Suzan",
    roles: ["Sales Associate", "Support Agent"],
    status: "Draft",
    conversations: 0,
    satisfaction: 0,
    aht: 0,
    escalations: 0,
    updatedAt: new Date(Date.now() - 1000 * 60 * 60 * 24).toISOString(),
    tone: "Friendly",
    traits: ["Persuasive", "Proactive"],
    escalationRule: "Never escalate",
    knowledge: { mode: 'select', collections: ['Pricing Guide'] },
    tools: { crm: true, knowledge: true, calendar: true, email: false },
    crmActions: { createCase: false, updateFields: false, addNote: true },
    knowledgeSettings: { topK: 5, recencyBias: 20, piiRedaction: false },
    routing: { workingHours: 'Mon–Fri 8:00–16:00' },
    resolvedCases: 0,
    openCases: 0,
    responseTimeSec: 0,
    kpis: ['Sales Conversion'],
  },
]

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

const secondsToText = (sec: number) => {
  if (!sec) return '—'
  if (sec < 90) return `${sec}s`
  const m = Math.floor(sec / 60)
  const s = sec % 60
  return `${m}m ${s}s`
}

const useDebounced = (val: string, delay = 300) => {
  const [d, setD] = React.useState(val);
  React.useEffect(() => { const id = setTimeout(() => setD(val), delay); return () => clearTimeout(id); }, [val, delay]);
  return d;
};

const Agents = () => {
  const [items, setItems] = React.useState<Agent[]>(seed);
  const [panelOpen, setPanelOpen] = React.useState(false);
  const [active, setActive] = React.useState<Agent | null>(null);
  const [tab, setTab] = React.useState<string>('overview');
  const [wizardOpen, setWizardOpen] = React.useState(false);
  const [wizardStep, setWizardStep] = React.useState<1|2|3|4|5>(1);
  const [wizard, setWizard] = React.useState<{ 
    name: string;
    roles: AgentRole[];
    tone?: AgentTone;
    traits: AgentTrait[];
    tools: { crm: boolean; knowledge: boolean; calendar: boolean; email: boolean };
    knowledge: { mode: 'all' | 'select'; collections: string[] };
    escalationRule?: EscalationRule;
  }>({
    name: '', roles: [], tone: undefined, traits: [], tools: { crm: true, knowledge: true, calendar: false, email: false }, knowledge: { mode: 'select', collections: [] }, escalationRule: undefined
  })
  const [query, setQuery] = React.useState("");
  const q = useDebounced(query, 300);
  const [status, setStatus] = React.useState<AgentStatus | "All">("All");
  const [pageSize, setPageSize] = React.useState<number>(() => Number(localStorage.getItem("agents.pageSize") || 25));
  const [page, setPage] = React.useState(1);
  const [isNarrow, setIsNarrow] = React.useState(false);
  const [viewportWidth, setViewportWidth] = React.useState(() => (typeof window !== 'undefined' ? window.innerWidth : 0));
  const [showEmbedCode, setShowEmbedCode] = React.useState(false);
  const [customKpiInput, setCustomKpiInput] = React.useState('');
  const panelWidth = React.useMemo(() => {
    if (isNarrow || !viewportWidth) return undefined;
    const sidebar = 264;
    const gutter = 24;
    const calculated = Math.max(viewportWidth - sidebar - gutter, 560);
    return `${Math.min(Math.round(calculated), 1080)}px`;
  }, [isNarrow, viewportWidth]);
  const embedScript = React.useMemo(() => `<script src="https://cdn.pocket.ai/widget.js" data-agent="${active?.id || 'AGENT_ID'}" async></script>`, [active?.id]);
  const selectedKpis = React.useMemo(() => active?.kpis ?? [], [active?.kpis]);
  const customKpis = React.useMemo(() => selectedKpis.filter(label => !COMMON_KPIS.includes(label)), [selectedKpis]);
  const knowledgeMode = React.useMemo(() => active?.knowledge?.mode ?? 'all', [active?.knowledge?.mode]);
  const knowledgeCollections = React.useMemo(() => active?.knowledge?.collections ?? [], [active?.knowledge?.collections]);
  const detailTabs: Array<{ value: 'overview' | 'kpis' | 'knowledge' | 'analytics'; label: string }> = React.useMemo(() => ([
    { value: 'overview', label: 'Overview' },
    { value: 'kpis', label: 'KPIs' },
    { value: 'knowledge', label: 'Knowledge' },
    { value: 'analytics', label: 'Analytics' },
  ]), []);

  const chipClasses = (active: boolean) =>
    cn(
      "h-8 px-3 text-xs sm:text-sm rounded-md transition-none focus-visible:ring-1 focus-visible:ring-primary/50",
      active
        ? "border border-primary/70 bg-primary text-primary-foreground shadow-sm"
        : "bg-input text-foreground/80 border-0 hover:bg-input"
    );

  type CapabilityKey = 'crm' | 'knowledge' | 'calendar' | 'email';
  const capabilityOptions: Array<{ key: CapabilityKey; title: string; description: string }> = [
    { key: 'crm', title: 'CRM', description: 'Create cases, log notes' },
    { key: 'knowledge', title: 'Knowledge', description: 'Use KB for answers' },
    { key: 'calendar', title: 'Calendar', description: 'Book meetings' },
    { key: 'email', title: 'Email', description: 'Send updates' },
  ];

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
  useEffect(() => {
    setShowEmbedCode(false);
    setCustomKpiInput('');
  }, [active?.id]);

  const filtered = React.useMemo(() => {
    let arr = [...items];
    if (status !== "All") arr = arr.filter((i) => i.status === status);
    if (q) {
      const needle = q.toLowerCase();
      arr = arr.filter((i) =>
        i.name.toLowerCase().includes(needle) ||
        i.id.toLowerCase().includes(needle) ||
        i.roles.join(" ").toLowerCase().includes(needle)
      );
    }
    // Default sort by updated desc
    arr.sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime());
    return arr;
  }, [items, status, q]);

  React.useEffect(() => setPage(1), [status, q, pageSize]);
  const total = filtered.length;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const pageItems = filtered.slice((page - 1) * pageSize, page * pageSize);

  const openPanel = (it: Agent) => { setActive(it); setPanelOpen(true); };
  const setStatusForIds = (ids: string[], s: AgentStatus) => setItems(prev => prev.map(i => ids.includes(i.id) ? { ...i, status: s, updatedAt: new Date().toISOString() } : i))
  const deleteIds = (ids: string[]) => setItems(prev => prev.filter(i => !ids.includes(i.id)))
  const updateActive = (patch: Partial<Agent>) => {
    if (!active) return
    const id = active.id
    setItems(prev => prev.map(i => i.id === id ? { ...i, ...patch, updatedAt: new Date().toISOString() } : i))
    setActive(a => a ? { ...a, ...patch, updatedAt: new Date().toISOString() } : a)
  }
  const duplicateAgent = (it: Agent) => {
    const id = `AG-${Math.floor(Math.random()*9000)+1000}`
    const copy: Agent = { ...it, id, name: `${it.name} Copy`, status: 'Draft', conversations: 0, satisfaction: 0, aht: 0, escalations: 0, updatedAt: new Date().toISOString() }
    setItems(prev => [copy, ...prev])
    setActive(copy); setPanelOpen(true)
  }

  const toggleKpiSelection = (label: string) => {
    if (!active) return
    const current = active.kpis || []
    const next = current.includes(label) ? current.filter(k => k !== label) : [...current, label]
    updateActive({ kpis: next })
  }

  const addCustomKpi = () => {
    if (!active) return
    const value = customKpiInput.trim()
    if (!value) return
    const current = active.kpis || []
    if (current.includes(value)) {
      setCustomKpiInput('')
      return
    }
    updateActive({ kpis: [...current, value] })
    setCustomKpiInput('')
  }

  const removeCustomKpi = (label: string) => {
    if (!active) return
    const current = active.kpis || []
    if (!current.includes(label)) return
    updateActive({ kpis: current.filter(k => k !== label) })
  }

  const setKnowledgeMode = (mode: 'all' | 'select') => {
    if (!active) return
    const knowledge = active.knowledge || { mode, collections: [] }
    updateActive({ knowledge: { ...knowledge, mode } })
  }

  const toggleKnowledgeCollection = (collection: string) => {
    if (!active) return
    const knowledge = active.knowledge || { mode: 'select', collections: [] }
    const current = knowledge.collections || []
    const next = current.includes(collection)
      ? current.filter((item) => item !== collection)
      : [...current, collection]
    updateActive({ knowledge: { ...knowledge, mode: 'select', collections: next } })
  }

  const resolvedCases = active
    ? typeof active.resolvedCases === 'number'
      ? active.resolvedCases
      : Math.max(0, (active.conversations ?? 0) - (active.openCases ?? active.escalations ?? 0))
    : null
  const openCases = active
    ? typeof active.openCases === 'number'
      ? active.openCases
      : active.escalations ?? 0
    : null
  const avgResponseSeconds = active ? (active.responseTimeSec ?? active.aht ?? 0) : 0
  const avgResponseLabel = secondsToText(avgResponseSeconds)
  const satisfactionValue = typeof active?.satisfaction === 'number' ? Math.max(0, Math.min(100, active.satisfaction)) : null
  const analyticsSummary: Array<{ key: 'conversations' | 'resolved' | 'open' | 'satisfaction' | 'response'; label: string; value: string }> = [
    {
      key: 'conversations',
      label: 'Conversations',
      value: typeof active?.conversations === 'number' ? active.conversations.toLocaleString() : '—',
    },
    {
      key: 'resolved',
      label: 'Resolved cases',
      value: resolvedCases != null ? resolvedCases.toLocaleString() : '—',
    },
    {
      key: 'open',
      label: 'Unresolved cases',
      value: openCases != null ? openCases.toLocaleString() : '—',
    },
    {
      key: 'satisfaction',
      label: 'Satisfaction',
      value: satisfactionValue != null ? `${satisfactionValue}%` : '—',
    },
    {
      key: 'response',
      label: 'Avg response time',
      value: avgResponseLabel,
    },
  ]

  const startWizard = () => { setWizardOpen(true); setWizardStep(1); setWizard({ name: '', roles: [], tone: undefined, traits: [], tools: { crm: true, knowledge: true, calendar: false, email: false }, knowledge: { mode: 'select', collections: [] }, escalationRule: undefined }) }
  const createFromWizard = (publish: boolean) => {
    const id = `AG-${Math.floor(Math.random()*9000)+1000}`
    const now = new Date().toISOString()
    const newAgent: Agent = {
      id,
      name: wizard.name.trim() || 'New Agent',
      roles: wizard.roles.length ? wizard.roles : ['Support Agent'],
      status: publish ? 'Active' : 'Draft',
      conversations: 0,
      satisfaction: 0,
      aht: 0,
      escalations: 0,
      updatedAt: now,
      tone: wizard.tone,
      traits: wizard.traits,
      escalationRule: wizard.escalationRule,
      knowledge: wizard.tools.knowledge ? wizard.knowledge : { mode: 'all', collections: [] },
      kpis: [...DEFAULT_KPI_SELECTION],
      resolvedCases: 0,
      openCases: 0,
      responseTimeSec: 0,
    }
    setItems(prev => [newAgent, ...prev])
    setWizardOpen(false)
    setActive(newAgent); setPanelOpen(true)
  }

  return (
    <div className="min-h-screen bg-background flex">
      <Sidebar />
      <main className="flex-1 overflow-auto">
        <div className="w-full px-4 md:px-6 lg:px-8 py-6">
          {/* Header */}
          <div className="mb-4 flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
            <div className="text-xl md:text-2xl font-semibold">Agents</div>
            <div className="flex items-center gap-2">
              <Button className="gap-2" onClick={startWizard}><Bot className="w-4 h-4" /> Create Agent</Button>
              <Button variant="outline" className="gap-2" disabled><Rocket className="w-4 h-4" /> Templates</Button>
            </div>
          </div>

          {/* Toolbar */}
          <div className="sticky top-0 z-10 bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60 border-y border-border/60">
            <div className="py-2 flex flex-col gap-2">
              <div className="flex flex-wrap items-center gap-2">
                {/* Search */}
                <div className="relative w-full sm:w-[220px] md:w-[240px]">
                  <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="Search name, ID, role…"
                    className="pl-9 border border-border/60 bg-muted/70 focus-visible:ring-0 focus-visible:border-border"
                  />
                </div>

                {/* Status chips */}
                <div className="flex items-center gap-2">
                  <Button size="sm" variant={status === 'All' ? 'default' : 'outline'} onClick={() => setStatus('All')}>All</Button>
                  <Button size="sm" variant={status === 'Active' ? 'default' : 'outline'} onClick={() => setStatus('Active')}>Active</Button>
                  <Button size="sm" variant={status === 'Inactive' ? 'default' : 'outline'} onClick={() => setStatus('Inactive')}>Inactive</Button>
                  <Button size="sm" variant={status === 'Draft' ? 'default' : 'outline'} onClick={() => setStatus('Draft')}>Draft</Button>
                </div>
              </div>
            </div>
          </div>

          {/* Table */}
          <div className="rounded-2xl bg-card/60 border border-border p-0 mt-3">
            <Table className="rounded-xl border border-border/70 bg-background/60 shadow-sm backdrop-blur [&_th]:px-3 [&_td]:px-3 [&_th:first-child]:pl-4 [&_td:first-child]:pl-4 [&_th:last-child]:pr-4 [&_td:last-child]:pr-4 [&_th]:py-3 [&_td]:py-3">
              <TableHeader className="sticky top-0 z-10 bg-muted/70 backdrop-blur border-b border-border/80">
                <TableRow className="hover:bg-transparent">
                  <TableHead className="w-[240px]">Name / ID</TableHead>
                  <TableHead className="w-[220px]">Roles</TableHead>
                  <TableHead className="w-[120px]">Status</TableHead>
                  <TableHead className="w-[140px] text-right">Conversations</TableHead>
                  <TableHead className="w-[120px] text-right">CSAT</TableHead>
                  <TableHead className="w-[120px] text-right">AHT</TableHead>
                  <TableHead className="w-[120px] text-right">Escalations</TableHead>
                  <TableHead className="w-[160px]">Updated</TableHead>
                  <TableHead className="w-10"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {pageItems.map((it) => (
                  <TableRow
                    key={it.id}
                    className="group cursor-pointer bg-transparent transition-colors hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-offset-0 focus-visible:ring-primary"
                    onClick={() => openPanel(it)}
                  >
                    <TableCell>
                      <div className="flex flex-col">
                        <div className="font-semibold text-foreground">{it.name}</div>
                        <div className="text-xs text-foreground/60">{it.id}</div>
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1.5">
                        {it.roles.map((r) => (
                          <Badge key={r} variant="secondary" className="text-xs">
                            {r}
                          </Badge>
                        ))}
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="inline-flex items-center gap-2 text-foreground/80">
                        <span
                          className={cn(
                            "h-2 w-2 rounded-full",
                            it.status === 'Active'
                              ? 'bg-emerald-500'
                              : it.status === 'Draft'
                                ? 'bg-amber-500'
                                : 'bg-muted-foreground'
                          )}
                        />
                        <span className="text-sm font-medium">{it.status}</span>
                      </div>
                    </TableCell>
                    <TableCell className="text-right text-foreground/80 tabular-nums">{it.conversations.toLocaleString()}</TableCell>
                    <TableCell className="text-right">
                      {it.satisfaction ? (
                        <Badge className="bg-emerald-500/15 text-emerald-600 dark:text-emerald-300 border border-emerald-500/25">
                          {it.satisfaction}%
                        </Badge>
                      ) : (
                        <span className="text-foreground/50">—</span>
                      )}
                    </TableCell>
                    <TableCell className="text-right text-foreground/80 tabular-nums">{secondsToText(it.aht)}</TableCell>
                    <TableCell className="text-right text-foreground/80 tabular-nums">{it.escalations}</TableCell>
                    <TableCell className="text-foreground/70">{formatRelative(it.updatedAt)}</TableCell>
                    <TableCell onClick={(e) => e.stopPropagation()}>
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <button className="p-1 rounded-md hover:bg-muted" aria-label="Row actions">
                            <EllipsisVertical className="w-4 h-4" />
                          </button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem onClick={() => openPanel(it)}>Open</DropdownMenuItem>
                          <DropdownMenuItem onClick={() => duplicateAgent(it)}>Duplicate</DropdownMenuItem>
                          {it.status !== 'Active' && (
                            <DropdownMenuItem onClick={() => setStatusForIds([it.id], 'Active')}>
                              Activate
                            </DropdownMenuItem>
                          )}
                          {it.status === 'Active' && (
                            <DropdownMenuItem onClick={() => setStatusForIds([it.id], 'Inactive')}>
                              Deactivate
                            </DropdownMenuItem>
                          )}
                          <DropdownMenuSeparator />
                          <DropdownMenuItem className="text-destructive" onClick={() => deleteIds([it.id])}>
                            Delete
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </TableCell>
                  </TableRow>
                ))}
                {pageItems.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={9}>
                      <div className="py-10 text-center text-sm text-muted-foreground">No agents match your filters.</div>
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>

            <div className="sticky bottom-0 z-10 border-t border-border/60 bg-background/95 backdrop-blur mt-3 py-2">
              <Pagination>
                <PaginationContent>
                  <PaginationItem>
                    <PaginationPrevious href="#" onClick={() => setPage(p => Math.max(1, p - 1))} />
                  </PaginationItem>
                  {Array.from({ length: pageCount }).map((_, i) => (
                    <PaginationItem key={i}>
                      <PaginationLink href="#" isActive={page === i + 1} onClick={() => setPage(i + 1)}>{i + 1}</PaginationLink>
                    </PaginationItem>
                  ))}
                  <PaginationItem>
                    <PaginationNext href="#" onClick={() => setPage(p => Math.min(pageCount, p + 1))} />
                  </PaginationItem>
                </PaginationContent>
              </Pagination>
            </div>
          </div>
        </div>
      </main>

      {/* Right Side Panel: Agent Detail */}
      <Sheet open={panelOpen} onOpenChange={setPanelOpen}>
        <SheetContent
          side="right"
          className={`${isNarrow ? 'w-screen' : 'sm:max-w-none max-w-none'} p-0`}
          style={isNarrow ? undefined : panelWidth ? { width: panelWidth } : undefined}
        >
          <div className="h-full flex flex-col">
            <div className="px-4 py-3 border-b border-border/60 bg-card/80 backdrop-blur">
              <SheetHeader>
                <SheetTitle className="flex flex-col gap-1">
                  <div className="text-xs text-muted-foreground tracking-wide">{active?.id}</div>
                  <div className="flex items-center gap-2">
                    <div className="flex items-center gap-2">
                      <Bot className="w-5 h-5 text-primary" />
                      <span className="text-lg font-semibold leading-none">{active?.name}</span>
                      {active?.status && (
                        <span className={`flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold ${
                          active.status === 'Active'
                            ? 'bg-emerald-500/15 text-emerald-300'
                            : active.status === 'Draft'
                              ? 'bg-amber-500/15 text-amber-200'
                              : 'bg-muted text-muted-foreground'
                        }`}>
                          <span
                            className={`h-2 w-2 rounded-full ${
                              active.status === 'Active'
                                ? 'bg-emerald-500'
                                : active.status === 'Draft'
                                  ? 'bg-amber-400'
                                  : 'bg-muted-foreground'
                            }`}
                          />
                          {active.status}
                        </span>
                      )}
                    </div>
                  </div>
                </SheetTitle>
              </SheetHeader>
            </div>

            <Tabs value={tab} onValueChange={(v) => setTab(v)} className="flex-1 flex flex-col">
              <div className="px-4 pt-3">
                <TabsList className="grid grid-cols-4 gap-2 w-full rounded-lg border border-border/40 bg-muted/70 p-1">
                  {detailTabs.map(({ value, label }) => (
                    <TabsTrigger
                      key={value}
                      value={value}
                      className="rounded-md px-3 py-2 text-xs font-semibold tracking-wide transition-colors data-[state=active]:bg-primary data-[state=active]:text-primary-foreground data-[state=active]:shadow-sm data-[state=inactive]:bg-transparent data-[state=inactive]:text-muted-foreground"
                    >
                      {label}
                    </TabsTrigger>
                  ))}
                </TabsList>
              </div>
              <div className="flex-1 min-h-0 p-4 space-y-4">
                <TabsContent value="overview">
                  <Card className="p-4 space-y-4">
                    <div>
                      <div className="text-sm font-semibold text-card-foreground">Agent basics</div>
                      <div className="text-xs text-muted-foreground mt-1">Snapshot of how this agent is configured.</div>
                    </div>
                    <div className="space-y-4 text-sm">
                      <div className="space-y-1">
                        <div className="text-xs font-semibold text-muted-foreground">Roles</div>
                        <div className="flex flex-wrap gap-2">{active?.roles.map(r => <Badge key={r} variant="secondary">{r}</Badge>)}</div>
                      </div>
                      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                        <div className="space-y-1">
                          <div className="text-xs font-semibold text-muted-foreground">Tone</div>
                          <div className="flex flex-wrap gap-2">
                            {active?.tone ? (
                              <Badge variant="secondary">{active.tone}</Badge>
                            ) : (
                              <span className="text-sm text-muted-foreground">Not set.</span>
                            )}
                          </div>
                        </div>
                        <div className="space-y-1">
                          <div className="text-xs font-semibold text-muted-foreground">Traits</div>
                          <div className="flex flex-wrap gap-2">
                            {active?.traits?.length ? active.traits.map(t => <Badge key={t} variant="secondary">{t}</Badge>) : <div className="text-sm">Not configured.</div>}
                          </div>
                        </div>
                      </div>
                      <div className="space-y-1">
                        <div className="text-xs font-semibold text-muted-foreground">Escalation rule</div>
                        <div className="flex flex-wrap gap-2">
                          {active?.escalationRule ? (
                            <Badge variant="secondary">{active.escalationRule}</Badge>
                          ) : (
                            <span className="text-sm text-muted-foreground">Not configured.</span>
                          )}
                        </div>
                      </div>
                    </div>
                  </Card>
                  <Card className="p-4 space-y-3">
                    <div className="text-xs font-semibold text-muted-foreground">Quick actions</div>
                    <div className="flex flex-wrap gap-2">
                      <Button size="sm" variant="outline" onClick={() => active && duplicateAgent(active)}>Duplicate</Button>
                      {active?.status !== 'Active' && <Button size="sm" onClick={() => { if (!active) return; setStatusForIds([active.id], 'Active') }}>Activate</Button>}
                      {active?.status === 'Active' && <Button size="sm" variant="outline" onClick={() => { if (!active) return; setStatusForIds([active.id], 'Inactive') }}>Deactivate</Button>}
                    </div>
                  </Card>
                  <Card className="p-4 space-y-4">
                    <div>
                      <div className="text-sm font-semibold text-card-foreground">Deployment</div>
                      <div className="text-xs text-muted-foreground mt-1">Share or embed this agent experience.</div>
                    </div>
                    <div className="space-y-3">
                      <div className="space-y-1">
                        <div className="text-xs font-semibold text-muted-foreground">Public link</div>
                        <div className="flex items-center gap-2">
                          <Input readOnly value={`https://pocket.ai/yourbusiness/${(active?.name || '').toLowerCase().replace(/[^a-z0-9\s-]/g,'').trim().replace(/\s+/g,'-') || 'agent'}`} />
                          <Button
                            variant="outline"
                            size="icon"
                            className="border-primary/40 bg-primary/5 text-primary hover:bg-primary/10 hover:border-primary/60"
                            onClick={() => navigator.clipboard?.writeText(`https://pocket.ai/yourbusiness/${(active?.name || '').toLowerCase().replace(/[^a-z0-9\s-]/g,'').trim().replace(/\s+/g,'-') || 'agent'}`)}
                            aria-label="Copy link"
                          >
                            <Copy className="w-4 h-4" />
                          </Button>
                        </div>
                      </div>
                      <div className="space-y-2">
                        <div className="text-xs font-semibold text-muted-foreground">Embed script</div>
                        <div className="flex flex-col gap-2">
                          <div className="flex items-center gap-2">
                            <div
                              className="flex h-10 w-full min-w-0 rounded-md border border-input bg-background px-3 py-2 text-xs font-mono text-foreground/80 whitespace-nowrap overflow-hidden text-ellipsis"
                              title={embedScript}
                            >
                              {embedScript}
                            </div>
                            <Button
                              variant="outline"
                              size="icon"
                              className={cn(
                                "border-primary/40 bg-primary/5 text-primary hover:bg-primary/10 hover:border-primary/60",
                                showEmbedCode && "bg-primary text-primary-foreground hover:bg-primary/90 hover:border-primary"
                              )}
                              onClick={() => setShowEmbedCode((v) => !v)}
                              aria-label={showEmbedCode ? "Hide embed script" : "View embed script"}
                            >
                              {showEmbedCode ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                            </Button>
                            <Button
                              variant="outline"
                              size="icon"
                              className="border-primary/40 bg-primary/5 text-primary hover:bg-primary/10 hover:border-primary/60"
                              onClick={() => navigator.clipboard?.writeText(embedScript)}
                              aria-label="Copy embed"
                            >
                              <Copy className="w-4 h-4" />
                            </Button>
                          </div>
                          {showEmbedCode && (
                            <div className="rounded-md border border-input bg-background px-3 py-2 font-mono text-xs leading-relaxed break-all">
                              {embedScript}
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  </Card>
                </TabsContent>

                <TabsContent value="knowledge">
                  <Card className="p-4 space-y-4">
                    <div>
                      <div className="text-sm font-semibold text-card-foreground">Knowledge</div>
                      <div className="text-xs text-muted-foreground mt-1">Choose how this agent accesses your collections.</div>
                    </div>
                    <div className="space-y-4">
                      <div className="flex gap-2 rounded-lg border border-border/40 bg-muted/70 p-1">
                        {(['all', 'select'] as const).map((mode) => {
                          const isActive = knowledgeMode === mode
                          return (
                            <button
                              key={mode}
                              type="button"
                              onClick={() => setKnowledgeMode(mode)}
                              className={cn(
                                "flex-1 rounded-md px-3 py-2 text-xs font-semibold transition-colors",
                                isActive
                                  ? "bg-primary text-primary-foreground shadow-sm"
                                  : "bg-transparent text-muted-foreground hover:text-primary"
                              )}
                            >
                              {mode === 'all' ? 'Grant all knowledge' : 'Select collections'}
                            </button>
                          )
                        })}
                      </div>

                      {knowledgeMode === 'all' ? (
                        <div className="text-xs text-muted-foreground">
                          This agent will have knowledge of all current and future files.
                        </div>
                      ) : (
                        <div className="space-y-3">
                          <div className="flex flex-wrap gap-2">
                            {KNOWLEDGE_COLLECTIONS.map((collection) => {
                              const selected = knowledgeCollections.includes(collection)
                              return (
                                <Button
                                  key={collection}
                                  type="button"
                                  size="sm"
                                  variant="outline"
                                  className={cn(
                                    "flex h-8 items-center gap-1.5 rounded-full border border-border/60 px-3 text-xs font-semibold transition-none",
                                    selected
                                      ? "bg-primary/10 text-primary border-primary/60 shadow-sm"
                                      : "text-muted-foreground hover:text-primary hover:border-primary/40"
                                  )}
                                  onClick={() => toggleKnowledgeCollection(collection)}
                                >
                                  {selected && <span className="inline-block h-1.5 w-1.5 rounded-full bg-primary" />}
                                  {collection}
                                </Button>
                              )
                            })}
                          </div>
                          <div className="text-xs text-muted-foreground">
                            {knowledgeCollections.length
                              ? `Selected: ${knowledgeCollections.length} ${knowledgeCollections.length === 1 ? 'collection' : 'collections'}`
                              : 'No collections selected yet.'}
                          </div>
                        </div>
                      )}
                    </div>
                  </Card>
                </TabsContent>

                <TabsContent value="kpis">
                  <Card className="p-4 space-y-4">
                    <div>
                      <div className="text-sm font-semibold text-card-foreground">KPIs</div>
                      <div className="text-xs text-muted-foreground mt-1">
                        Select the metrics you want the agent to prioritize during chats.
                      </div>
                    </div>
                    <div className="space-y-2">
                      <div className="text-xs font-semibold text-muted-foreground">Common KPIs</div>
                      <div className="flex flex-wrap gap-2">
                        {COMMON_KPIS.map((label) => {
                          const selected = selectedKpis.includes(label)
                          return (
                            <Button
                              key={label}
                              type="button"
                              size="sm"
                              variant="outline"
                              className={cn(
                                "flex h-8 items-center gap-1.5 rounded-full border border-border/60 px-3 text-xs font-semibold transition-none",
                                selected
                                  ? "bg-primary/10 text-primary border-primary/60 shadow-sm"
                                  : "text-muted-foreground hover:text-primary hover:border-primary/40"
                              )}
                              onClick={() => toggleKpiSelection(label)}
                            >
                              {selected && <span className="inline-block h-1.5 w-1.5 rounded-full bg-primary" />}
                              {label}
                            </Button>
                          )
                        })}
                      </div>
                    </div>
                    <div className="space-y-2">
                      <div className="text-xs font-semibold text-muted-foreground">Custom KPI</div>
                      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                        <Input
                          value={customKpiInput}
                          onChange={(e) => setCustomKpiInput(e.target.value)}
                          placeholder="Add custom KPI"
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') {
                              e.preventDefault()
                              addCustomKpi()
                            }
                          }}
                        />
                        <Button
                          type="button"
                          variant="outline"
                          size="icon"
                          className="border-primary/40 bg-primary/5 text-primary hover:bg-primary/10 hover:border-primary/60 disabled:opacity-50"
                          onClick={addCustomKpi}
                          disabled={!customKpiInput.trim()}
                          aria-label="Add custom KPI"
                        >
                          <Plus className="h-4 w-4" />
                        </Button>
                      </div>
                      {customKpis.length > 0 ? (
                        <div className="flex flex-wrap gap-2">
                          {customKpis.map((label) => (
                            <div
                              key={label}
                              className="flex items-center gap-2 rounded-full border border-primary/50 bg-primary/10 px-3 py-1.5 text-xs font-semibold text-primary"
                            >
                              <span className="inline-block h-1.5 w-1.5 rounded-full bg-primary" />
                              <span className="max-w-[160px] truncate">{label}</span>
                              <Button
                                type="button"
                                variant="ghost"
                                size="icon"
                                className="h-7 w-7 text-muted-foreground hover:text-foreground"
                                aria-label={`Remove ${label}`}
                                onClick={() => removeCustomKpi(label)}
                              >
                                <X className="h-3.5 w-3.5" />
                              </Button>
                            </div>
                          ))}
                        </div>
                      ) : null}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      Your selected KPIs will guide response strategies and prioritization. Backend integration to follow.
                    </div>
                  </Card>
                </TabsContent>

                <TabsContent value="analytics">
                  <Card className="p-4 space-y-4">
                    <div>
                      <div className="text-sm font-semibold text-card-foreground">Analytics</div>
                      <div className="text-xs text-muted-foreground mt-1">Quick snapshot of performance metrics.</div>
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      {analyticsSummary.map((item) => {
                        if (item.key === 'satisfaction') {
                          const progress = satisfactionValue ?? 0
                          return (
                            <div key={item.key} className="rounded-lg border border-border/40 bg-muted/70 p-3 flex flex-col gap-2">
                              <div className="text-xs text-muted-foreground">{item.label}</div>
                              <div className="text-2xl font-semibold">{item.value}</div>
                              <div className="h-1.5 rounded-full bg-border/60 overflow-hidden">
                                <div
                                  className="h-full rounded-full bg-primary transition-all"
                                  style={{ width: `${Math.min(100, Math.max(0, progress))}%` }}
                                />
                              </div>
                            </div>
                          )
                        }

                        return (
                          <div key={item.key} className="rounded-lg border border-border/40 bg-muted/70 p-3 flex flex-col gap-2">
                            <div className="text-xs text-muted-foreground">{item.label}</div>
                            <div className="text-2xl font-semibold">{item.value}</div>
                          </div>
                        )
                      })}
                    </div>
                  </Card>
                </TabsContent>
              </div>
            </Tabs>

            <div className="px-4 py-3 border-t border-border/60 flex items-center justify-end gap-2">
              <Button onClick={() => setPanelOpen(false)}>Close</Button>
            </div>
          </div>
        </SheetContent>
      </Sheet>
      {/* Create Agent Wizard */}
      <Dialog open={wizardOpen} onOpenChange={setWizardOpen}>
        <DialogContent className="sm:max-w-[840px] max-h-[90vh] overflow-hidden p-0">
          <div className="flex flex-col h-full">
            <div className="px-4 py-3 border-b border-border/60 bg-card/80 backdrop-blur">
              <DialogHeader>
                <DialogTitle>Create Agent</DialogTitle>
              </DialogHeader>
            </div>

            <div className="flex-1 min-h-0 p-4 overflow-auto">

              {wizardStep === 1 && (
                <Card className="p-4 bg-card/80 border border-border/60 shadow-sm">
                  <div className="mb-4">
                    <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Step 1</div>
                    <div className="text-lg font-semibold text-card-foreground">Basics</div>
                  </div>
                  <div className="space-y-3">
                    <div>
                      <div className="mb-2 text-xs text-muted-foreground">Agent Name</div>
                      <Input
                        value={wizard.name}
                        onChange={(e) => setWizard(w => ({ ...w, name: e.target.value }))}
                        placeholder="e.g. Nancy"
                        className="bg-input border-0 focus-visible:ring-1 focus-visible:ring-primary/50"
                      />
                    </div>
                    <Separator className="my-2" />
                    <div>
                      <div className="mb-2 text-xs text-muted-foreground">Tone</div>
                      <div className="flex flex-wrap gap-2">
                        {AGENT_TONES.map(tn => {
                          const active = wizard.tone === tn;
                          return (
                            <Button
                              key={tn}
                              size="sm"
                              variant={active ? "default" : "outline"}
                              className={chipClasses(active)}
                              onClick={() => setWizard(w => ({ ...w, tone: tn }))}
                            >
                              {tn}
                            </Button>
                          );
                        })}
                      </div>
                    </div>
                  </div>
                  <Separator className="my-3" />
                  <div className="mb-2 text-xs text-muted-foreground">Roles</div>
                  <div className="flex flex-wrap gap-2">
                    {AGENT_ROLES.map(r => {
                      const selected = wizard.roles.includes(r);
                      return (
                        <Button
                          key={r}
                          size="sm"
                          variant={selected ? "default" : "outline"}
                          className={chipClasses(selected)}
                          onClick={() => setWizard(w => ({ ...w, roles: selected ? w.roles.filter(x => x !== r) : [...w.roles, r] }))}
                        >
                          {r}
                        </Button>
                      );
                    })}
                  </div>
                  <Separator className="my-3" />
                  <div className="mb-2 text-xs text-muted-foreground">Traits</div>
                  <div className="flex flex-wrap gap-2">
                    {AGENT_TRAITS.map(tr => {
                      const selected = wizard.traits.includes(tr);
                      return (
                        <Button
                          key={tr}
                          size="sm"
                          variant={selected ? "default" : "outline"}
                          className={chipClasses(selected)}
                          onClick={() => setWizard(w => ({ ...w, traits: selected ? w.traits.filter(x => x !== tr) : [...w.traits, tr] }))}
                        >
                          {tr}
                        </Button>
                      );
                    })}
                  </div>
                  <div className="mt-4 bg-muted/60 border border-border/40 rounded-md px-3 py-2 flex items-start gap-2">
                    <Info className="mt-0.5 h-3.5 w-3.5 text-muted-foreground/80" aria-hidden="true" />
                    <div className="text-xs text-muted-foreground">
                      {wizard.knowledge.mode === 'all'
                        ? 'All knowledge is available. Your agent stays in sync as new content is added.'
                        : 'Select only the collections this agent should reference. You can adjust the list anytime.'}
                    </div>
                  </div>
                </Card>
              )}

              {wizardStep === 2 && (
                <Card className="p-4 bg-card/80 border border-border/60 shadow-sm">
                  <div className="mb-4">
                    <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Step 2</div>
                    <div className="text-lg font-semibold text-card-foreground">Capabilities</div>
                  </div>
                  <div className="space-y-3">
                    {capabilityOptions.map((opt, index) => (
                      <React.Fragment key={opt.key}>
                        <div className="flex items-center justify-between gap-4">
                          <div>
                            <div className="mb-1 text-xs text-muted-foreground uppercase tracking-wide">{opt.title}</div>
                            <div className="text-xs text-muted-foreground/80">{opt.description}</div>
                          </div>
                          <Switch
                            checked={wizard.tools[opt.key]}
                            onCheckedChange={(v) => setWizard(w => ({ ...w, tools: { ...w.tools, [opt.key]: !!v } }))}
                          />
                        </div>
                        {index < capabilityOptions.length - 1 && <Separator className="my-2" />}
                      </React.Fragment>
                    ))}
                  </div>
                </Card>
              )}

              {wizardStep === 3 && (
                <Card className="p-4 bg-card/80 border border-border/60 shadow-sm">
                  <div className="mb-4">
                    <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Step 3</div>
                    <div className="text-lg font-semibold text-card-foreground">Knowledge</div>
                  </div>
                  <div className="space-y-3">
                    <div>
                      <div className="mb-2 text-xs text-muted-foreground">Knowledge Access</div>
                      <div className="flex w-full items-center gap-1 rounded-lg border border-border/40 bg-muted/70 p-1">
                        {([
                          { value: 'all', label: 'Grant all access' },
                          { value: 'select', label: 'Select collections' },
                        ] as const).map(({ value, label }) => {
                          const active = wizard.knowledge.mode === value
                          return (
                            <button
                              key={value}
                              type="button"
                              onClick={() => setWizard(w => ({ ...w, knowledge: { ...w.knowledge, mode: value } }))}
                              className={cn(
                                "flex-1 rounded-md px-3 py-2 text-xs font-semibold text-center",
                                active
                                  ? "bg-primary text-primary-foreground shadow-sm ring-1 ring-primary/50"
                                  : "bg-muted/40 text-foreground/75"
                              )}
                            >
                              {label}
                            </button>
                          )
                        })}
                      </div>
                    </div>

                    {wizard.knowledge.mode === 'select' && (
                      <div className="flex flex-wrap gap-2">
                        {['Product KB', 'Support SOPs', 'Security Policies', 'Pricing Guide', 'Onboarding Handbook'].map(col => {
                          const selected = wizard.knowledge.collections.includes(col)
                          return (
                            <Button
                              key={col}
                              size="sm"
                              variant={selected ? "default" : "outline"}
                              className={chipClasses(selected)}
                              onClick={() => setWizard(w => ({ ...w, knowledge: { ...w.knowledge, collections: selected ? w.knowledge.collections.filter(x => x !== col) : [...w.knowledge.collections, col] } }))}
                            >
                              {col}
                            </Button>
                          )
                        })}
                      </div>
                    )}

                    <div className="bg-muted/60 border border-border/40 rounded-md px-3 py-2 flex items-start gap-2">
                      <Info className="mt-0.5 h-3.5 w-3.5 text-muted-foreground/80" aria-hidden="true" />
                      <div className="text-xs text-muted-foreground">
                        {wizard.knowledge.mode === 'all'
                          ? 'All knowledge is available. Your agent stays in sync as new content is added.'
                          : 'Select only the collections this agent should reference. You can adjust the list anytime.'}
                      </div>
                    </div>
                  </div>
                </Card>
              )}

              {wizardStep === 4 && (
                <Card className="p-4 bg-card/80 border border-border/60 shadow-sm">
                  <div className="mb-4">
                    <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Step 4</div>
                    <div className="text-lg font-semibold text-card-foreground">Routing & Escalation</div>
                  </div>
                  <div className="mb-2 text-xs text-muted-foreground">Routing & Escalation</div>
                  <div className="flex flex-wrap gap-2">
                    {ESCALATION_OPTIONS.map(opt => {
                      const active = wizard.escalationRule === opt;
                      return (
                        <Button
                          key={opt}
                          size="sm"
                          variant={active ? "default" : "outline"}
                          className={chipClasses(active)}
                          onClick={() => setWizard(w => ({ ...w, escalationRule: opt }))}
                        >
                          {opt}
                        </Button>
                      );
                    })}
                  </div>
                  <div className="mt-4 bg-muted/60 border border-border/40 rounded-md px-3 py-2 flex items-start gap-2">
                    <Info className="mt-0.5 h-3.5 w-3.5 text-muted-foreground/80" aria-hidden="true" />
                    <div className="text-xs text-muted-foreground">
                      Set when to hand off to a human. You can refine this later.
                    </div>
                  </div>
                </Card>
              )}

              {wizardStep === 5 && (
                <Card className="p-4 bg-card/80 border border-border/60 shadow-sm">
                  <div className="mb-4">
                    <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Step 5</div>
                    <div className="text-lg font-semibold text-card-foreground">Review</div>
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
                    <div>
                      <div className="text-xs text-muted-foreground">Name</div>
                      <div className="font-semibold">{wizard.name || '—'}</div>
                    </div>
                    <div>
                      <div className="text-xs text-muted-foreground">Roles</div>
                      <div>{wizard.roles.length ? wizard.roles.join(' • ') : '—'}</div>
                    </div>
                    <div>
                      <div className="text-xs text-muted-foreground">Tone</div>
                      <div>{wizard.tone || '—'}</div>
                    </div>
                    <div>
                      <div className="text-xs text-muted-foreground">Traits</div>
                      <div>{wizard.traits.length ? wizard.traits.join(', ') : '—'}</div>
                    </div>
                    <div>
                      <div className="text-xs text-muted-foreground">Tools</div>
                      <div>
                        {(() => {
                          const activeTools = Object.entries(wizard.tools)
                            .filter(([, v]) => v)
                            .map(([k]) => {
                              if (k.toLowerCase() === 'crm') return 'CRM'
                              return k
                                .split(/[-_]/)
                                .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
                                .join(' ')
                            })
                          return activeTools.length ? activeTools.join(', ') : '—'
                        })()}
                      </div>
                    </div>
                    <div>
                      <div className="text-xs text-muted-foreground">Knowledge</div>
                      <div>{wizard.knowledge.mode === 'all' ? 'All collections' : `${wizard.knowledge.collections.length} collections`}</div>
                    </div>
                    <div>
                      <div className="text-xs text-muted-foreground">Escalation</div>
                      <div>{wizard.escalationRule || '—'}</div>
                    </div>
                  </div>
                </Card>
              )}
            </div>

            <div className="px-4 py-3 border-t border-border/60 flex items-center justify-between">
              <div className="flex items-center gap-2">
                {wizardStep > 1 ? (
                  <Button variant="outline" onClick={() => setWizardStep((wizardStep-1) as any)}>Back</Button>
                ) : null}
              </div>
              <div className="flex items-center gap-2">
                {wizardStep < 5 && (
                  <Button onClick={() => setWizardStep((wizardStep+1) as any)} disabled={(wizardStep === 1 && (!wizard.name.trim() || wizard.roles.length === 0))}>Next</Button>
                )}
                {wizardStep === 5 && (
                  <>
                    <Button variant="outline" onClick={() => createFromWizard(false)}>Create Draft</Button>
                    <Button onClick={() => createFromWizard(true)}>Create & Publish</Button>
                  </>
                )}
              </div>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default Agents;
