import React from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Separator } from "@/components/ui/separator";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import {
  Pagination,
  PaginationContent,
  PaginationItem,
  PaginationLink,
  PaginationNext,
  PaginationPrevious,
} from "@/components/ui/pagination";
import { cn } from "@/lib/utils";
import {
  Building2,
  CalendarDays,
  ChevronDown,
  Flame,
  Mail,
  MessageCircle,
  Phone,
  Plus,
  Search,
  Target,
  Upload,
  Users,
} from "lucide-react";

type LeadStage = "New" | "Qualified" | "Engaged" | "Negotiation" | "Closed Won";
type LeadChannel = "email" | "call" | "chat";
type LeadHeat = "all" | "hot" | "warm" | "cold";

type LeadTimelineItem = {
  id: string;
  at: string;
  actor: string;
  summary: string;
  channel: LeadChannel;
  notes?: string;
};

type Lead = {
  id: string;
  name: string;
  company: string;
  stage: LeadStage;
  owner: string;
  ownerAvatar?: string;
  source: string;
  lastTouch: string;
  score: number;
  email: string;
  phone?: string;
  tags: string[];
  value: number;
  notes: string;
  timeline: LeadTimelineItem[];
  location?: string;
  slaHours: number;
};

const leadsSeed: Lead[] = [
  {
    id: "LD-401",
    name: "Amelia Stone",
    company: "Northwind Labs",
    stage: "Qualified",
    owner: "Alex Rivera",
    ownerAvatar: "https://api.dicebear.com/7.x/avataaars/svg?seed=Alex",
    source: "Website",
    lastTouch: new Date(Date.now() - 1000 * 60 * 60 * 5).toISOString(),
    score: 86,
    email: "amelia@northwindlabs.com",
    phone: "+1 (415) 555-2234",
    tags: ["AI Automation", "Enterprise"],
    value: 48000,
    notes: "Interested in AI agent rollout for tier-1 support. Needs security review next week.",
    slaHours: 2.3,
    timeline: [
      {
        id: "LD-401-t3",
        at: new Date(Date.now() - 1000 * 60 * 45).toISOString(),
        actor: "Nancy AI",
        summary: "Sent tailored demo workspace recap and implementation plan.",
        channel: "email",
      },
      {
        id: "LD-401-t2",
        at: new Date(Date.now() - 1000 * 60 * 60 * 5).toISOString(),
        actor: "Alex Rivera",
        summary: "Logged discovery call—focus on 24/7 onboarding coverage.",
        channel: "call",
      },
      {
        id: "LD-401-t1",
        at: new Date(Date.now() - 1000 * 60 * 60 * 26).toISOString(),
        actor: "Website Form",
        summary: "Lead captured via automation playbook download.",
        channel: "chat",
      },
    ],
    location: "San Francisco, USA",
  },
  {
    id: "LD-402",
    name: "Marcus Lee",
    company: "Helios Ventures",
    stage: "Engaged",
    owner: "Leena Patel",
    ownerAvatar: "https://api.dicebear.com/7.x/avataaars/svg?seed=Leena",
    source: "Partner referral",
    lastTouch: new Date(Date.now() - 1000 * 60 * 90).toISOString(),
    score: 79,
    email: "marcus@helios.vc",
    phone: "+1 (212) 555-8911",
    tags: ["VC Portfolio", "Growth"],
    value: 32000,
    notes: "Needs ROI case study ahead of board sync. Interested in co-pilot for portfolio companies.",
    slaHours: 1.8,
    timeline: [
      {
        id: "LD-402-t2",
        at: new Date(Date.now() - 1000 * 60 * 90).toISOString(),
        actor: "Leena Patel",
        summary: "Shared customer story deck tailored to venture funds.",
        channel: "email",
      },
      {
        id: "LD-402-t1",
        at: new Date(Date.now() - 1000 * 60 * 60 * 30).toISOString(),
        actor: "Referral",
        summary: "Introduced by Horizon CX partners—warm lead.",
        channel: "chat",
      },
    ],
    location: "New York, USA",
  },
  {
    id: "LD-403",
    name: "Priya Nambiar",
    company: "Skyline Retail",
    stage: "Negotiation",
    owner: "Alex Rivera",
    ownerAvatar: "https://api.dicebear.com/7.x/avataaars/svg?seed=Alexandra",
    source: "Outbound",
    lastTouch: new Date(Date.now() - 1000 * 60 * 60 * 2).toISOString(),
    score: 91,
    email: "priya.nambiar@skyline.com",
    phone: "+44 20 7946 1280",
    tags: ["Retail", "Global"],
    value: 78000,
    notes: "Legal review in progress. Wants managed support handoff in EMEA.",
    slaHours: 2.1,
    timeline: [
      {
        id: "LD-403-t3",
        at: new Date(Date.now() - 1000 * 60 * 30).toISOString(),
        actor: "Nancy AI",
        summary: "Provided GDPR compliance summary via automation follow-up.",
        channel: "email",
      },
      {
        id: "LD-403-t2",
        at: new Date(Date.now() - 1000 * 60 * 60 * 2).toISOString(),
        actor: "Alex Rivera",
        summary: "Negotiation call—discussed 6-month pilot scope.",
        channel: "call",
      },
      {
        id: "LD-403-t1",
        at: new Date(Date.now() - 1000 * 60 * 60 * 50).toISOString(),
        actor: "Outbound",
        summary: "Prospecting sequence initiated with tailored retail automation use cases.",
        channel: "email",
      },
    ],
    location: "London, UK",
  },
  {
    id: "LD-404",
    name: "Jonah Patel",
    company: "Atlas Hardware",
    stage: "New",
    owner: "Maya Chen",
    ownerAvatar: "https://api.dicebear.com/7.x/avataaars/svg?seed=Maya",
    source: "Event: CX Summit",
    lastTouch: new Date(Date.now() - 1000 * 60 * 60 * 60).toISOString(),
    score: 54,
    email: "jonah.patel@atlashw.com",
    tags: ["Manufacturing"],
    value: 18000,
    notes: "Requested pricing sheet—waiting on internal approval to proceed to discovery call.",
    slaHours: 3.4,
    timeline: [
      {
        id: "LD-404-t1",
        at: new Date(Date.now() - 1000 * 60 * 60 * 60).toISOString(),
        actor: "Event Capture",
        summary: "Met at CX Summit booth—scanned badge for follow-up.",
        channel: "chat",
      },
    ],
    location: "Austin, USA",
  },
  {
    id: "LD-405",
    name: "Sofia García",
    company: "Globo Logistics",
    stage: "Closed Won",
    owner: "Leena Patel",
    ownerAvatar: "https://api.dicebear.com/7.x/avataaars/svg?seed=Sofia",
    source: "Customer referral",
    lastTouch: new Date(Date.now() - 1000 * 60 * 60 * 4).toISOString(),
    score: 95,
    email: "sofia.garcia@globologistics.com",
    phone: "+52 55 1234 9087",
    tags: ["Logistics", "Premium"],
    value: 92000,
    notes: "Signed annual agreement. Implementation kickoff set for next Monday.",
    slaHours: 1.2,
    timeline: [
      {
        id: "LD-405-t3",
        at: new Date(Date.now() - 1000 * 60 * 25).toISOString(),
        actor: "Leena Patel",
        summary: "Submitted onboarding checklist and assigned success manager.",
        channel: "email",
      },
      {
        id: "LD-405-t2",
        at: new Date(Date.now() - 1000 * 60 * 60 * 4).toISOString(),
        actor: "Sofia García",
        summary: "Signed contract via e-sign link.",
        channel: "chat",
      },
      {
        id: "LD-405-t1",
        at: new Date(Date.now() - 1000 * 60 * 60 * 72).toISOString(),
        actor: "Customer Referral",
        summary: "Referred by Globex Ops—requested automation audit.",
        channel: "email",
      },
    ],
    location: "Mexico City, MX",
  },
  {
    id: "LD-406",
    name: "Noah Becker",
    company: "Crescent Health",
    stage: "Engaged",
    owner: "Maya Chen",
    ownerAvatar: "https://api.dicebear.com/7.x/avataaars/svg?seed=Noah",
    source: "Outbound",
    lastTouch: new Date(Date.now() - 1000 * 60 * 60 * 8).toISOString(),
    score: 68,
    email: "noah.becker@crescenthealth.org",
    phone: "+1 (646) 555-9034",
    tags: ["Healthcare", "Compliance"],
    value: 41000,
    notes: "Security questionnaire pending. Interested in HIPAA workflows & redaction.",
    slaHours: 2.8,
    timeline: [
      {
        id: "LD-406-t2",
        at: new Date(Date.now() - 1000 * 60 * 180).toISOString(),
        actor: "Maya Chen",
        summary: "Shared HIPAA automation blueprint.",
        channel: "email",
      },
      {
        id: "LD-406-t1",
        at: new Date(Date.now() - 1000 * 60 * 60 * 8).toISOString(),
        actor: "Noah Becker",
        summary: "Requested risk assessment deck before legal review.",
        channel: "chat",
      },
    ],
    location: "Chicago, USA",
  },
];

const stageStyles: Record<LeadStage, string> = {
  New: "bg-slate-100 text-slate-600 border border-slate-200 dark:bg-transparent dark:text-slate-300 dark:border-slate-600/70",
  Qualified:
    "bg-indigo-100 text-indigo-600 border border-indigo-200 dark:bg-transparent dark:text-indigo-200 dark:border-indigo-500/65",
  Engaged:
    "bg-blue-100 text-blue-600 border border-blue-200 dark:bg-transparent dark:text-blue-200 dark:border-blue-500/65",
  Negotiation:
    "bg-purple-100 text-purple-600 border border-purple-200 dark:bg-transparent dark:text-purple-200 dark:border-purple-500/65",
  "Closed Won":
    "bg-emerald-100 text-emerald-600 border border-emerald-200 dark:bg-transparent dark:text-emerald-200 dark:border-emerald-500/65",
};

const Sidebar = () => {
  const items = [
    { label: "Home", to: "/dashboard" },
    { label: "Cases", to: "/dashboard/cases" },
    { label: "Leads", to: "/dashboard/leads", active: true },
    { label: "Customers", to: "/dashboard/customers" },
    { label: "Agents", to: "/dashboard/agents" },
    { label: "Knowledge", to: "/dashboard/knowledge" },
  ];

  return (
    <aside className="hidden md:flex w-56 shrink-0 border-r border-border/70 bg-card/60 backdrop-blur-sm h-screen sticky top-0 overflow-hidden sidebar-card-chrome">
      <div className="flex flex-col w-full p-3 gap-2">
        <div className="px-2 py-3 text-lg font-semibold">
          Dashboard <span className="text-xs align-top text-muted-foreground">v0.1</span>
        </div>
        <nav className="mt-1 flex-1 space-y-1">
          {items.map((item) => (
            <Link
              key={item.label}
              to={item.to}
              className={cn(
                "w-full block text-left px-3 py-2 rounded-md text-sm flex items-center justify-between",
                item.active ? "bg-primary/10 text-primary" : "hover:bg-muted/60"
              )}
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

const heatOptions: { label: string; value: LeadHeat; icon?: React.ReactNode }[] = [
  { label: "All", value: "all" },
  { label: "Hot", value: "hot", icon: <Flame className="w-3.5 h-3.5" /> },
  { label: "Warm", value: "warm", icon: <Target className="w-3.5 h-3.5" /> },
  { label: "Cold", value: "cold", icon: <Users className="w-3.5 h-3.5" /> },
];

const formatRelative = (iso: string) => {
  const date = new Date(iso);
  const diff = Date.now() - date.getTime();
  const mins = Math.round(diff / 60000);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.round(days / 30);
  return `${months}mo ago`;
};

const getChannelIcon = (channel: LeadChannel) => {
  switch (channel) {
    case "email":
      return Mail;
    case "call":
      return Phone;
    case "chat":
      return MessageCircle;
    default:
      return MessageCircle;
  }
};

const scoreTone = (score: number) => {
  if (score >= 80) return "text-primary";
  if (score >= 60) return "text-amber-600 dark:text-amber-300";
  return "text-muted-foreground";
};

const formatCurrency = (value: number) =>
  new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(value);

const Leads = () => {
  const [leads] = React.useState<Lead[]>(leadsSeed);
  const [search, setSearch] = React.useState("");
  const [stageFilter, setStageFilter] = React.useState<LeadStage | "all">("all");
  const [ownerFilter, setOwnerFilter] = React.useState<string>("all");
  const [rangeFilter, setRangeFilter] = React.useState<string>("14");
  const [heat, setHeat] = React.useState<LeadHeat>("all");
  const [selectedLead, setSelectedLead] = React.useState<Lead | null>(null);
  const [isDrawerOpen, setIsDrawerOpen] = React.useState(false);

  const owners = React.useMemo(() => Array.from(new Set(leads.map((lead) => lead.owner))), [leads]);
  const stages = React.useMemo(() => Array.from(new Set(leads.map((lead) => lead.stage))), [leads]);

  const pageSize = 25;
  const [page, setPage] = React.useState(1);

  const filteredLeads = React.useMemo(() => {
    const normalizedSearch = search.trim().toLowerCase();
    const days = rangeFilter === "all" ? null : Number(rangeFilter);

    return leads
      .filter((lead) => {
        if (stageFilter !== "all" && lead.stage !== stageFilter) return false;
        if (ownerFilter !== "all" && lead.owner !== ownerFilter) return false;
        if (normalizedSearch) {
          const haystack = `${lead.name} ${lead.company} ${lead.owner} ${lead.tags.join(" ")}`.toLowerCase();
          if (!haystack.includes(normalizedSearch)) return false;
        }
        if (days !== null) {
          const diffDays = (Date.now() - new Date(lead.lastTouch).getTime()) / (1000 * 60 * 60 * 24);
          if (diffDays > days) return false;
        }
        switch (heat) {
          case "hot":
            return lead.score >= 80;
          case "warm":
            return lead.score >= 60 && lead.score < 80;
          case "cold":
            return lead.score < 60;
          default:
            return true;
        }
      })
      .sort((a, b) => b.score - a.score);
  }, [heat, leads, ownerFilter, rangeFilter, search, stageFilter]);

  const pageCount = React.useMemo(() => Math.max(1, Math.ceil(filteredLeads.length / pageSize)), [filteredLeads.length, pageSize]);
  const pageItems = React.useMemo(() => filteredLeads.slice((page - 1) * pageSize, page * pageSize), [filteredLeads, page, pageSize]);

  React.useEffect(() => {
    setPage(1);
  }, [search, stageFilter, ownerFilter, rangeFilter, heat]);

  React.useEffect(() => {
    setPage((prev) => Math.min(prev, pageCount));
  }, [pageCount]);

  const openLeads = React.useMemo(() => leads.filter((lead) => lead.stage !== "Closed Won"), [leads]);
  const hotCount = React.useMemo(() => leads.filter((lead) => lead.score >= 80 && lead.stage !== "Closed Won").length, [leads]);
  const avgSla = React.useMemo(() => {
    if (!openLeads.length) return "—";
    const avg =
      openLeads.reduce((acc, lead) => acc + lead.slaHours, 0) / openLeads.length;
    return `${avg.toFixed(1)}h`;
  }, [openLeads]);

  

  const pipelineBreakdown = React.useMemo(() => {
    const totals: Record<LeadStage, number> = {
      New: 0,
      Qualified: 0,
      Engaged: 0,
      Negotiation: 0,
      "Closed Won": 0,
    };
    leads.forEach((lead) => {
      totals[lead.stage] += 1;
    });
    const total = leads.length || 1;
    return (Object.keys(totals) as LeadStage[]).map((stage) => ({
      stage,
      value: totals[stage],
      percentage: Math.round((totals[stage] / total) * 100),
    }));
  }, [leads]);

  

  const handleOpenLead = (lead: Lead) => {
    setSelectedLead(lead);
    setIsDrawerOpen(true);
  };

  return (
    <div className="min-h-screen bg-background flex">
      <Sidebar />
      <main className="flex-1 overflow-auto">
        <div className="w-full px-4 md:px-6 lg:px-8 py-6">
          <section className="flex flex-col gap-6">
            <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
              <div className="space-y-2">
                <div className="text-xl md:text-2xl font-semibold tracking-tight text-gradient-hero">Leads</div>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Button className="gap-2">
                  <Plus className="w-4 h-4" />
                  New Lead
                </Button>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="outline" className="gap-2">
                      <Upload className="w-4 h-4" />
                      Import
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
              <StatCard label="Open leads" value={`${openLeads.length}`} delta="Up 18% vs last week" />
              <StatCard label="Hot leads" value={`${hotCount}`} delta="3 ready for next step" />
              <StatCard label="Avg. response SLA" value={avgSla} delta="< 3h target" />
            </div>

            <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
              <div className="flex flex-col gap-3 md:flex-row md:items-center md:flex-wrap">
                <div className="relative w-full md:w-64">
                  <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                    placeholder="Search lead, company, or tag"
                    className="pl-9 border border-border/60 bg-muted/70 focus-visible:ring-0 focus-visible:border-border"
                  />
                </div>
              </div>
              <div className="flex items-center gap-2 flex-wrap">
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="outline" className="gap-2 text-sm">
                      <Building2 className="w-4 h-4" />
                      {stageFilter === "all" ? "All stages" : stageFilter}
                      <ChevronDown className="w-4 h-4 text-muted-foreground" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="start" className="w-44">
                    <DropdownMenuItem onClick={() => setStageFilter("all")}>All stages</DropdownMenuItem>
                    <DropdownMenuSeparator />
                    {stages.map((stage) => (
                      <DropdownMenuItem key={stage} onClick={() => setStageFilter(stage)}>
                        {stage}
                      </DropdownMenuItem>
                    ))}
                  </DropdownMenuContent>
                </DropdownMenu>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="outline" className="gap-2 text-sm">
                      <CalendarDays className="w-4 h-4" />
                      {rangeFilter === "all" ? "Any time" : `Last ${rangeFilter} days`}
                      <ChevronDown className="w-4 h-4 text-muted-foreground" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="start" className="w-40">
                    <DropdownMenuItem onClick={() => setRangeFilter("7")}>Last 7 days</DropdownMenuItem>
                    <DropdownMenuItem onClick={() => setRangeFilter("14")}>Last 14 days</DropdownMenuItem>
                    <DropdownMenuItem onClick={() => setRangeFilter("30")}>Last 30 days</DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem onClick={() => setRangeFilter("all")}>All time</DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
                {heatOptions.map((option) => (
                  <Button
                    key={option.value}
                    onClick={() => setHeat(option.value)}
                    size="sm"
                    variant={heat === option.value ? "default" : "outline"}
                    className={cn(
                      "rounded-full px-4 transition-all",
                      heat === option.value
                        ? "bg-primary text-primary-foreground shadow-sm"
                        : "bg-background/80 text-muted-foreground hover:text-foreground"
                    )}
                  >
                    <span className="flex items-center gap-1.5 text-xs font-medium">
                      {option.icon}
                      {option.label}
                    </span>
                  </Button>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-12 gap-4">
              <div className="col-span-12 space-y-4">
                <Card className="relative border border-border/70 bg-card shadow-lg shadow-black/5 backdrop-blur hover:border-primary/35 transition-colors dark:border-slate-700/50 dark:bg-slate-800/70 dark:shadow-black/25">
                  <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-primary/35 via-primary/20 to-transparent" />
                  {filteredLeads.length === 0 ? (
                    <div className="py-14 flex flex-col items-center justify-center gap-3">
                      <Users className="w-8 h-8 text-muted-foreground/60" />
                      <div className="text-sm font-medium">No leads match this view yet</div>
                      <p className="text-xs text-muted-foreground max-w-sm text-center">
                        Adjust filters or sync more sources to discover opportunities ready for nurture.
                      </p>
                      <div className="flex gap-2">
                        <Button size="sm" className="gap-1.5">
                          <Plus className="w-3.5 h-3.5" />
                          New lead
                        </Button>
                        <Button size="sm" variant="outline">
                          Reset filters
                        </Button>
                      </div>
                    </div>
                  ) : (
                    <>
                      <div className="hidden md:block">
                        <div className="relative overflow-x-auto overflow-y-auto max-h-[70vh]">
                          <Table className="rounded-xl border border-border/70 bg-background/60 shadow-sm backdrop-blur [&_th]:px-3 [&_td]:px-3 [&_th:first-child]:pl-4 [&_td:first-child]:pl-4 [&_th:last-child]:pr-4 [&_td:last-child]:pr-4 [&_th]:py-3 [&_td]:py-3">
                            <TableHeader className="sticky top-0 z-10 bg-muted/70 backdrop-blur border-b border-border/80">
                              <TableRow className="hover:bg-transparent">
                                <TableHead className="w-[180px]">Lead</TableHead>
                                <TableHead>Company</TableHead>
                                <TableHead>Stage</TableHead>
                                <TableHead>Owner</TableHead>
                                <TableHead>Source</TableHead>
                                <TableHead>Last touch</TableHead>
                                <TableHead className="text-right">Score</TableHead>
                              </TableRow>
                            </TableHeader>
                            <TableBody>
                              {pageItems.map((lead) => (
                                <TableRow
                                  key={lead.id}
                                  onClick={() => handleOpenLead(lead)}
                                  className="group cursor-pointer bg-transparent transition-colors hover:bg-muted/60 dark:hover:bg-white/10 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-offset-0 focus-visible:ring-primary"
                                >
                                  <TableCell>
                                    <div className="flex flex-col">
                                      <span className="text-sm font-semibold">{lead.name}</span>
                                      <span className="text-xs text-muted-foreground">{lead.email}</span>
                                    </div>
                                  </TableCell>
                                  <TableCell className="text-sm text-muted-foreground">{lead.company}</TableCell>
                                  <TableCell>
                                    <Badge variant="outline" className={cn("text-xs", stageStyles[lead.stage])}>{lead.stage}</Badge>
                                  </TableCell>
                                  <TableCell>
                                    <div className="flex items-center gap-2">
                                      {lead.ownerAvatar ? (
                                        <Avatar className="h-6 w-6">
                                          <AvatarImage src={lead.ownerAvatar} alt={lead.owner} />
                                          <AvatarFallback>{lead.owner.slice(0, 2)}</AvatarFallback>
                                        </Avatar>
                                      ) : null}
                                      <span className="text-sm">{lead.owner}</span>
                                    </div>
                                  </TableCell>
                                  <TableCell className="text-sm text-muted-foreground">{lead.source}</TableCell>
                                  <TableCell className="text-sm text-muted-foreground">{formatRelative(lead.lastTouch)}</TableCell>
                                  <TableCell className="text-right">
                                    <span className={cn("text-sm font-semibold", scoreTone(lead.score))}>{lead.score}</span>
                                  </TableCell>
                                </TableRow>
                              ))}
                            </TableBody>
                          </Table>
                        </div>
                      </div>
                      <div className="md:hidden p-4 pt-0 space-y-3">
                        {pageItems.map((lead) => (
                          <Card
                            key={lead.id}
                            className={cn(
                              "border border-border/70 bg-card/90",
                              lead.score >= 80 && "border-primary/40"
                            )}
                          >
                            <div className="p-3 space-y-2">
                              <div className="flex items-start justify-between gap-3">
                                <div>
                                  <div className="text-sm font-semibold leading-tight">{lead.name}</div>
                                  <div className="text-xs text-muted-foreground">{lead.company}</div>
                                </div>
                                <Badge variant="outline" className={cn("text-[10px]", stageStyles[lead.stage])}>{lead.stage}</Badge>
                              </div>
                              <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                                <span className="font-medium text-foreground">{lead.owner}</span>
                                <span>•</span>
                                <span>{lead.source}</span>
                                <span>•</span>
                                <span>{formatRelative(lead.lastTouch)}</span>
                              </div>
                              <div className="flex items-center justify-between">
                                <div className="flex flex-wrap gap-1">
                                  {lead.tags.map((tag) => (
                                    <Badge key={tag} variant="outline" className="text-[10px]">
                                      {tag}
                                    </Badge>
                                  ))}
                                </div>
                                <div className={cn("text-sm font-semibold", scoreTone(lead.score))}>{lead.score}</div>
                              </div>
                              <Button size="sm" variant="outline" className="w-full" onClick={() => handleOpenLead(lead)}>
                                View details
                              </Button>
                            </div>
                          </Card>
                        ))}
                      </div>
                    </>
                  )}
                </Card>
                <div className="sticky bottom-0 z-10 border-t border-border/60 bg-background/95 backdrop-blur mt-3 py-2">
                  <Pagination>
                    <PaginationContent>
                      <PaginationItem>
                        <PaginationPrevious onClick={() => setPage((prev) => Math.max(1, prev - 1))} href="#" />
                      </PaginationItem>
                      {Array.from({ length: pageCount }).map((_, index) => (
                        <PaginationItem key={index}>
                          <PaginationLink href="#" isActive={page === index + 1} onClick={() => setPage(index + 1)}>
                            {index + 1}
                          </PaginationLink>
                        </PaginationItem>
                      ))}
                      <PaginationItem>
                        <PaginationNext onClick={() => setPage((prev) => Math.min(pageCount, prev + 1))} href="#" />
                      </PaginationItem>
                    </PaginationContent>
                  </Pagination>
                </div>
              </div>
            </div>

            
          </section>
        </div>
      </main>

      <Sheet open={isDrawerOpen} onOpenChange={setIsDrawerOpen}>
        <SheetContent side="right" className="w-full sm:max-w-md overflow-y-auto border-l border-border/60 bg-card/95 backdrop-blur">
          {selectedLead ? (
            <div className="space-y-5">
              <SheetHeader>
                <SheetTitle className="flex flex-col gap-1 text-left">
                  <span>{selectedLead.name}</span>
                  <span className="text-sm text-muted-foreground">{selectedLead.company}</span>
                </SheetTitle>
                <SheetDescription className="text-left">
                  {selectedLead.tags.join(" • ")} · {selectedLead.email}
                </SheetDescription>
              </SheetHeader>

              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="outline" className={cn("text-xs", stageStyles[selectedLead.stage])}>{selectedLead.stage}</Badge>
                <Badge variant="outline" className="text-xs">
                  Score {selectedLead.score}
                </Badge>
                <Badge variant="outline" className="text-xs">
                  {formatRelative(selectedLead.lastTouch)}
                </Badge>
              </div>

              <Card className="border border-border/70 bg-muted/70">
                <div className="p-4 space-y-3 text-sm">
                  <div className="flex items-center justify-between">
                    <span className="text-muted-foreground">Owner</span>
                    <div className="flex items-center gap-2">
                      {selectedLead.ownerAvatar ? (
                        <Avatar className="h-6 w-6">
                          <AvatarImage src={selectedLead.ownerAvatar} alt={selectedLead.owner} />
                          <AvatarFallback>{selectedLead.owner.slice(0, 2)}</AvatarFallback>
                        </Avatar>
                      ) : null}
                      <span className="font-medium text-foreground">{selectedLead.owner}</span>
                    </div>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-muted-foreground">Potential value</span>
                    <span className="font-medium">{formatCurrency(selectedLead.value)}</span>
                  </div>
                  {selectedLead.location ? (
                    <div className="flex items-center justify-between">
                      <span className="text-muted-foreground">Region</span>
                      <span className="font-medium">{selectedLead.location}</span>
                    </div>
                  ) : null}
                </div>
              </Card>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                <Button variant="outline" className="gap-2 text-xs">
                  <Mail className="w-4 h-4" />
                  Send email
                </Button>
                <Button variant="outline" className="gap-2 text-xs">
                  <Phone className="w-4 h-4" />
                  Log call
                </Button>
                <Button variant="outline" className="gap-2 text-xs">
                  <CalendarDays className="w-4 h-4" />
                  Book meeting
                </Button>
                <Button variant="outline" className="gap-2 text-xs">
                  <MessageCircle className="w-4 h-4" />
                  Add note
                </Button>
              </div>

              <Card className="border border-border/70 bg-muted/70">
                <div className="p-4 space-y-3">
                  <div className="text-sm font-semibold">Notes</div>
                  <p className="text-sm text-muted-foreground leading-relaxed">
                    {selectedLead.notes}
                  </p>
                </div>
              </Card>

              <Card className="border border-border/70 bg-muted/70">
                <div className="p-4 space-y-4">
                  <div className="text-sm font-semibold">Timeline</div>
                  <div className="space-y-3">
                    {selectedLead.timeline.map((entry, index) => {
                      const Icon = getChannelIcon(entry.channel);
                      return (
                        <React.Fragment key={entry.id}>
                          <div className="flex items-start gap-3">
                            <div className="h-8 w-8 rounded-lg border border-border/60 bg-muted/40 grid place-items-center">
                              <Icon className="w-4 h-4 text-primary" />
                            </div>
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center justify-between text-xs text-muted-foreground">
                                <span>{entry.actor}</span>
                                <span>{formatRelative(entry.at)}</span>
                              </div>
                              <div className="text-sm text-foreground leading-snug">{entry.summary}</div>
                              {entry.notes ? (
                                <div className="text-xs text-muted-foreground mt-1">{entry.notes}</div>
                              ) : null}
                            </div>
                          </div>
                          {index < selectedLead.timeline.length - 1 && <Separator />}
                        </React.Fragment>
                      );
                    })}
                  </div>
                </div>
              </Card>
            </div>
          ) : (
            <div className="flex items-center justify-center h-full text-sm text-muted-foreground">
              Select a lead to view the enriched profile.
            </div>
          )}
        </SheetContent>
      </Sheet>
    </div>
  );
};

export default Leads;

