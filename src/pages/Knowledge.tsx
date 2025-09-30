import React from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Progress } from "@/components/ui/progress";
import { Switch } from "@/components/ui/switch";
import {
  UploadCloud,
  FileText,
  FileSpreadsheet,
  FileBarChart,
  Database,
  CheckCircle2,
  AlertTriangle,
  MoreHorizontal,
  RefreshCw,
  Link as LinkIcon,
  Plug,
  ShieldCheck,
  Layers,
  Clock,
  Star,
  HelpCircle,
  Search,
} from "lucide-react";
import { cn } from "@/lib/utils";

type DocumentStatus = "ready" | "processing" | "error";

type KnowledgeDocument = {
  id: string;
  name: string;
  type: "pdf" | "doc" | "text" | "xlsx" | "csv" | "url";
  size: string;
  tags: string[];
  status: DocumentStatus;
  progress?: number;
  updatedAt: string;
  source: string;
  classification: string;
  owner: string;
  collections: string[];
  summary: string;
};

type KnowledgeIntegration = {
  id: string;
  name: string;
  description: string;
  status: "connected" | "disconnected" | "beta";
  icon: React.ReactNode;
  lastSync?: string;
  scope?: string;
};

type KnowledgeCollection = {
  id: string;
  name: string;
  description: string;
  documents: number;
  lastUpdated: string;
  visibility: "private" | "shared" | "public";
  focus?: string;
  owner: string;
};

const documentSeeds: KnowledgeDocument[] = [
  {
    id: "DOC-301",
    name: "Support SOP Handbook",
    type: "pdf",
    size: "3.2 MB",
    tags: ["SOP", "Support", "Internal"],
    status: "ready",
    updatedAt: "2 hours ago",
    source: "Manual upload",
    classification: "SOP",
    owner: "Adham",
    collections: ["Support SOPs", "All Access"],
    summary:
      "Step-by-step workflows for level 1 & level 2 support teams, including escalation policies and customer voice guidelines.",
  },
  {
    id: "DOC-287",
    name: "Product Pricing Catalog",
    type: "xlsx",
    size: "940 KB",
    tags: ["Pricing", "Catalog", "Sales"],
    status: "processing",
    progress: 68,
    updatedAt: "Just now",
    source: "Google Drive",
    classification: "Product Catalog",
    owner: "Leena",
    collections: ["Sales Enablement"],
    summary:
      "Tiered pricing matrix with regional adjustments, promotional bundles, and wholesale discounts for Q4 2024.",
  },
  {
    id: "DOC-275",
    name: "Mission & Vision Statement",
    type: "text",
    size: "12 KB",
    tags: ["Culture", "Strategy"],
    status: "ready",
    updatedAt: "Yesterday",
    source: "Text paste",
    classification: "Mission & Vision",
    owner: "CX Ops",
    collections: ["Brand Voice", "All Access"],
    summary:
      "Narrative overview of company mission pillars, brand values, tone, and customer promise language for AI training.",
  },
  {
    id: "DOC-268",
    name: "Warranty & Returns Policy",
    type: "doc",
    size: "1.1 MB",
    tags: ["Policy", "Legal"],
    status: "error",
    updatedAt: "3 days ago",
    source: "SharePoint",
    classification: "Warranty Policy",
    owner: "Legal Team",
    collections: ["Policies"],
    summary:
      "Warranty coverage definitions, return windows, restocking fees, and exception handling for premium customers.",
  },
];

const integrationSeeds: KnowledgeIntegration[] = [
  {
    id: "int-1",
    name: "Google Drive",
    description: "Sync folders containing policies, playbooks, and spreadsheets.",
    status: "connected",
    icon: <Layers className="w-5 h-5 text-primary" />, 
    lastSync: "14 minutes ago",
    scope: "Customer Success › SOPs",
  },
  {
    id: "int-2",
    name: "Notion",
    description: "Import knowledge bases, wiki pages, and product docs.",
    status: "beta",
    icon: <FileText className="w-5 h-5 text-primary" />, 
    lastSync: "Awaiting first sync",
    scope: "Team workspace › Support KB",
  },
  {
    id: "int-3",
    name: "SharePoint",
    description: "Centralize compliance and legal templates.",
    status: "disconnected",
    icon: <ShieldCheck className="w-5 h-5 text-primary" />, 
    lastSync: "Never",
  },
  {
    id: "int-4",
    name: "Custom API",
    description: "Bring proprietary knowledge sources via JSON schema.",
    status: "connected",
    icon: <Plug className="w-5 h-5 text-primary" />, 
    lastSync: "51 minutes ago",
    scope: "Knowledge Hub API v2",
  },
  {
    id: "int-5",
    name: "Excel / CSV",
    description: "Automate ingestion of live operational spreadsheets.",
    status: "connected",
    icon: <FileSpreadsheet className="w-5 h-5 text-primary" />, 
    lastSync: "3 hours ago",
    scope: "Orders + Returns ledger",
  },
  {
    id: "int-6",
    name: "S3 Bucket",
    description: "Ingest archived PDF manuals and product catalogs.",
    status: "disconnected",
    icon: <Database className="w-5 h-5 text-primary" />, 
    lastSync: "Never",
  },
];

const collectionSeeds: KnowledgeCollection[] = [
  {
    id: "col-1",
    name: "Support SOPs",
    description: "Step-by-step playbooks for tiered support + escalation rules.",
    documents: 24,
    lastUpdated: "Updated 1 hour ago",
    visibility: "shared",
    owner: "Nancy AI",
    focus: "Support & CX",
  },
  {
    id: "col-2",
    name: "Policies",
    description: "Legal & compliance documents for all customer touch points.",
    documents: 18,
    lastUpdated: "Updated yesterday",
    visibility: "private",
    owner: "Legal Team",
    focus: "Compliance",
  },
  {
    id: "col-3",
    name: "Sales Enablement",
    description: "Pricing tables, objection handling, and positioning briefs.",
    documents: 32,
    lastUpdated: "Updated 22 minutes ago",
    visibility: "shared",
    owner: "Revenue Ops",
    focus: "Growth",
  },
  {
    id: "col-4",
    name: "All Access",
    description: "Default pool of knowledge available to all agents.",
    documents: 56,
    lastUpdated: "Synced 6 minutes ago",
    visibility: "public",
    owner: "Pocket AI",
    focus: "Global",
  },
];

const documentTypeLabel = (type: KnowledgeDocument["type"]) => {
  switch (type) {
    case "pdf":
      return "PDF";
    case "doc":
      return "Doc";
    case "text":
      return "Text";
    case "xlsx":
      return "Excel";
    case "csv":
      return "CSV";
    case "url":
      return "Link";
    default:
      return type;
  }
};

const statusBadgeClasses = (status: DocumentStatus) => {
  switch (status) {
    case "ready":
      return "bg-emerald-500/15 text-emerald-400 border border-emerald-500/30";
    case "processing":
      return "bg-primary/15 text-primary border border-primary/30";
    case "error":
      return "bg-destructive/15 text-destructive border border-destructive/30";
    default:
      return "bg-muted text-muted-foreground";
  }
};

const Sidebar = () => {
  const items = [
    { label: "Home", to: "/dashboard" },
    { label: "Cases", to: "/dashboard/cases" },
    { label: "Leads", to: "/dashboard/leads" },
    { label: "Customers", to: "/dashboard/customers" },
    { label: "Agents", to: "/dashboard/agents" },
    { label: "Knowledge", to: "/dashboard/knowledge", active: true },
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

const StatBadge = ({ label, value, icon }: { label: string; value: string; icon: React.ReactNode }) => (
  <Card className="flex flex-col gap-2 border border-border/60 bg-card/70 p-4">
    <div className="flex items-center gap-3">
      <div className="h-10 w-10 rounded-full bg-primary/10 text-primary grid place-items-center">
        {icon}
      </div>
      <div>
        <div className="text-xs uppercase tracking-wide text-muted-foreground">{label}</div>
        <div className="text-xl font-semibold leading-tight">{value}</div>
      </div>
    </div>
  </Card>
);

const Knowledge = () => {
  const [tab, setTab] = React.useState<"documents" | "integrations" | "collections">("documents");
  const [documents] = React.useState<KnowledgeDocument[]>(documentSeeds);
  const [integrations] = React.useState<KnowledgeIntegration[]>(integrationSeeds);
  const [collections] = React.useState<KnowledgeCollection[]>(collectionSeeds);
  const [search, setSearch] = React.useState("");
  const [activeDoc, setActiveDoc] = React.useState<KnowledgeDocument | null>(documentSeeds[0] || null);
  const [panelOpen, setPanelOpen] = React.useState(false);
  const [isNarrow, setIsNarrow] = React.useState(false);

  React.useEffect(() => {
    const mq = window.matchMedia("(max-width: 1023px)");
    const onChange = () => setIsNarrow(mq.matches);
    mq.addEventListener("change", onChange);
    setIsNarrow(mq.matches);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const filteredDocs = React.useMemo(() => {
    if (!search) return documents;
    const term = search.toLowerCase();
    return documents.filter((doc) =>
      [doc.name, doc.source, doc.tags.join(" "), doc.collections.join(" ")]
        .join(" ")
        .toLowerCase()
        .includes(term)
    );
  }, [documents, search]);

  const openDocument = (doc: KnowledgeDocument) => {
    setActiveDoc(doc);
    if (isNarrow) {
      setPanelOpen(true);
    }
  };

  return (
    <div className="min-h-screen bg-background flex">
      <Sidebar />
      <main className="flex-1 overflow-auto">
        <div className="w-full px-4 md:px-6 lg:px-8 py-6">
          <header className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">
            <div className="inline-flex items-center gap-2 rounded-full border border-primary/30 bg-primary/5 px-3 py-1 text-xs font-semibold text-primary uppercase tracking-wide">
              Knowledge Hub
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Button variant="outline" className="gap-2">
                <LinkIcon className="w-4 h-4" /> Connect integration
              </Button>
              <Button className="gap-2">
                <UploadCloud className="w-4 h-4" /> Upload content
              </Button>
            </div>
          </header>

          <Tabs value={tab} onValueChange={(v) => setTab(v as typeof tab)} className="mt-6">
              <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <TabsList className="rounded-lg border border-border/40 bg-muted/70 p-1 flex">
                  <TabsTrigger
                    value="documents"
                    className="rounded-md px-3 py-1.5 text-xs font-semibold tracking-wide transition-colors data-[state=active]:bg-primary data-[state=active]:text-primary-foreground data-[state=active]:shadow-sm data-[state=inactive]:bg-transparent data-[state=inactive]:text-muted-foreground"
                  >
                    Documents
                  </TabsTrigger>
                  <TabsTrigger
                    value="integrations"
                    className="rounded-md px-3 py-1.5 text-xs font-semibold tracking-wide transition-colors data-[state=active]:bg-primary data-[state=active]:text-primary-foreground data-[state=active]:shadow-sm data-[state=inactive]:bg-transparent data-[state=inactive]:text-muted-foreground"
                  >
                    Integrations
                  </TabsTrigger>
                  <TabsTrigger
                    value="collections"
                    className="rounded-md px-3 py-1.5 text-xs font-semibold tracking-wide transition-colors data-[state=active]:bg-primary data-[state=active]:text-primary-foreground data-[state=active]:shadow-sm data-[state=inactive]:bg-transparent data-[state=inactive]:text-muted-foreground"
                  >
                    Collections
                  </TabsTrigger>
                </TabsList>
              {tab === "documents" && (
                <div className="flex flex-1 flex-col gap-2 sm:flex-row sm:items-center sm:justify-end">
                  <div className="relative w-full md:w-64">
                    <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                    <Input
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                      placeholder="Search documents…"
                      className="pl-9 bg-input border-0"
                    />
                  </div>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="outline" className="gap-2">
                        <Layers className="w-4 h-4" /> Collections
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="w-52">
                      {collections.map((col) => (
                        <DropdownMenuItem key={col.id} className="text-sm">
                          {col.name}
                        </DropdownMenuItem>
                      ))}
                      <Separator className="my-1" />
                      <DropdownMenuItem className="text-sm text-primary">Manage collections…</DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
              )}
              {tab === "integrations" && (
                <div className="flex flex-wrap items-center gap-2">
                  <Button variant="outline" size="sm" className="gap-2">
                    <RefreshCw className="w-4 h-4" /> Sync all
                  </Button>
                  <Button variant="outline" size="sm" className="gap-1">
                    <CheckCircle2 className="w-4 h-4" /> Show connected only
                  </Button>
                </div>
              )}
              {tab === "collections" && (
                <div className="flex flex-wrap items-center gap-2">
                  <Button size="sm">Sort by activity</Button>
                  <Button size="sm">New collection</Button>
                </div>
              )}
            </div>

            <TabsContent value="documents" className="mt-5">
              <div className="space-y-4">
                  <Card className="border-dashed border-2 border-primary/30 bg-primary/5 p-6">
                    <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
                      <div className="flex items-start gap-4">
                        <div className="rounded-full bg-primary/10 text-primary p-3">
                          <UploadCloud className="w-5 h-5" />
                        </div>
                        <div>
                          <div className="text-sm font-semibold">Drag & drop files or paste text</div>
                          <p className="text-xs text-muted-foreground">
                            Upload PDF, Word, Markdown, or spreadsheets. You can also paste raw text for quick knowledge entries.
                          </p>
                          <div className="mt-2 flex flex-wrap gap-2">
                            <Button size="sm" variant="secondary" className="gap-2">
                              <FileText className="w-4 h-4" /> Add text note
                            </Button>
                            <Button size="sm" variant="secondary" className="gap-2">
                              <FileSpreadsheet className="w-4 h-4" /> Import table
                            </Button>
                          </div>
                        </div>
                      </div>
                    </div>
                  </Card>

                  <Card className="border border-border/60 bg-card/70">
                    <ScrollArea className="max-h-[520px]">
                      <Table className="rounded-xl border border-border/70 bg-background/60 shadow-sm backdrop-blur [&_th]:px-3 [&_td]:px-3 [&_th:first-child]:pl-4 [&_td:first-child]:pl-4 [&_th:last-child]:pr-4 [&_td:last-child]:pr-4 [&_th]:py-3 [&_td]:py-3">
                        <TableHeader className="sticky top-0 z-10 bg-muted/70 backdrop-blur border-b border-border/80">
                          <TableRow className="hover:bg-transparent">
                            <TableHead className="w-[220px]">Name</TableHead>
                            <TableHead>Classification</TableHead>
                            <TableHead>Type</TableHead>
                            <TableHead>Status</TableHead>
                            <TableHead className="text-right">Updated</TableHead>
                            <TableHead className="w-[40px]" />
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {filteredDocs.map((doc) => (
                            <TableRow
                              key={doc.id}
                              className={cn(
                                "group cursor-pointer bg-transparent transition-colors hover:bg-muted/60 dark:hover:bg-white/10 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-offset-0 focus-visible:ring-primary"
                              )}
                              onClick={() => openDocument(doc)}
                            >
                              <TableCell>
                                <div className="flex flex-col">
                                  <span className="font-medium leading-tight">{doc.name}</span>
                                  <span className="text-xs text-muted-foreground">{doc.id}</span>
                                </div>
                              </TableCell>
                              <TableCell>
                                <div className="text-sm text-muted-foreground">
                                  {doc.classification}
                                </div>
                              </TableCell>
                              <TableCell>
                                <Badge variant="secondary" className="capitalize">
                                  {documentTypeLabel(doc.type)}
                                </Badge>
                              </TableCell>
                              <TableCell>
                                <div className="flex flex-col gap-1 text-xs items-start">
                                  <span className={cn("inline-flex w-fit items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold", statusBadgeClasses(doc.status))}>
                                    {doc.status === "ready" && <CheckCircle2 className="w-3 h-3" />}
                                    {doc.status === "processing" && <RefreshCw className="w-3 h-3 animate-spin" />}
                                    {doc.status === "error" && <AlertTriangle className="w-3 h-3" />}
                                    <span className="capitalize">{doc.status}</span>
                                  </span>
                                  {doc.status === "error" && (
                                    <span className="text-destructive">Check source permission</span>
                                  )}
                                </div>
                              </TableCell>
                              <TableCell className="text-right">
                                <div className="text-sm font-medium">{doc.updatedAt}</div>
                                <div className="text-xs text-muted-foreground">{doc.size}</div>
                              </TableCell>
                              <TableCell onClick={(e) => e.stopPropagation()}>
                                <DropdownMenu>
                                  <DropdownMenuTrigger asChild>
                                    <button className="p-1.5 rounded-md hover:bg-muted" aria-label="Document actions">
                                      <MoreHorizontal className="w-4 h-4" />
                                    </button>
                                  </DropdownMenuTrigger>
                                  <DropdownMenuContent align="end" className="w-44">
                                    <DropdownMenuItem onClick={() => openDocument(doc)}>Preview</DropdownMenuItem>
                                    <DropdownMenuItem>Download</DropdownMenuItem>
                                    <DropdownMenuItem>Duplicate</DropdownMenuItem>
                                    <DropdownMenuItem>Assign to collection…</DropdownMenuItem>
                                    <Separator className="my-1" />
                                    <DropdownMenuItem className="text-destructive">Delete</DropdownMenuItem>
                                  </DropdownMenuContent>
                                </DropdownMenu>
                              </TableCell>
                            </TableRow>
                          ))}
                          {filteredDocs.length === 0 && (
                            <TableRow>
                              <TableCell colSpan={8} className="py-12 text-center text-sm text-muted-foreground">
                                No documents match your filters yet.
                              </TableCell>
                            </TableRow>
                          )}
                        </TableBody>
                      </Table>
                    </ScrollArea>
                  </Card>
              </div>
            </TabsContent>

            <TabsContent value="integrations" className="mt-5">
              <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-3">
                {integrations.map((integration) => (
                  <Card key={integration.id} className={cn("border border-border/60 bg-card/70 p-4 flex flex-col gap-4", integration.status === "disconnected" && "border-dashed border-primary/40")}
                  >
                    <div className="flex items-start gap-3">
                      <div className="h-10 w-10 rounded-full bg-primary/10 grid place-items-center">
                        {integration.icon}
                      </div>
                      <div className="flex-1 space-y-1">
                        <div className="flex items-center gap-2">
                          <div className="text-sm font-semibold">{integration.name}</div>
                          <Badge variant="secondary" className="capitalize">
                            {integration.status}
                          </Badge>
                        </div>
                        <p className="text-xs text-muted-foreground leading-relaxed">{integration.description}</p>
                        {integration.scope && (
                          <div className="inline-flex items-center gap-1 rounded-full border border-border/60 bg-muted/60 px-2 py-1 text-[11px] text-muted-foreground">
                            <Layers className="w-3 h-3" /> {integration.scope}
                          </div>
                        )}
                      </div>
                    </div>
                    <div className="flex items-center justify-between text-xs text-muted-foreground">
                      <span>Last sync</span>
                      <span className="text-foreground font-medium">{integration.lastSync}</span>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Button size="sm" variant="outline" className="gap-2">
                        {integration.status === "connected" ? <RefreshCw className="w-4 h-4" /> : <Plug className="w-4 h-4" />}
                        {integration.status === "connected" ? "Sync now" : "Connect"}
                      </Button>
                      <Button size="sm" variant="ghost" className="text-xs">
                        Configure access
                      </Button>
                    </div>
                  </Card>
                ))}
              </div>
            </TabsContent>

            <TabsContent value="collections" className="mt-5">
              <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-3">
                {collections.map((collection) => (
                  <Card key={collection.id} className="border border-border/60 bg-card/70 p-4 flex flex-col gap-4">
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <div className="text-sm font-semibold">{collection.name}</div>
                        <Badge variant="outline" className="capitalize text-[11px]">
                          {collection.visibility}
                        </Badge>
                      </div>
                      <p className="text-xs text-muted-foreground leading-relaxed">{collection.description}</p>
                    </div>
                    <div className="grid grid-cols-2 gap-2 text-xs text-muted-foreground">
                      <div className="rounded-md border border-border/60 bg-muted/40 px-3 py-2">
                        <div className="font-medium text-sm text-foreground">{collection.documents}</div>
                        <div>Documents</div>
                      </div>
                      <div className="rounded-md border border-border/60 bg-muted/40 px-3 py-2">
                        <div className="font-medium text-sm text-foreground">{collection.owner}</div>
                        <div>Owner</div>
                      </div>
                      <div className="rounded-md border border-border/60 bg-muted/40 px-3 py-2">
                        <div className="font-medium text-sm text-foreground">{collection.focus}</div>
                        <div>Focus</div>
                      </div>
                      <div className="rounded-md border border-border/60 bg-muted/40 px-3 py-2">
                        <div className="font-medium text-sm text-foreground">{collection.lastUpdated}</div>
                        <div>Last update</div>
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Button size="sm" className="gap-2">
                        <Layers className="w-4 h-4" /> Manage access
                      </Button>
                      <Button size="sm" variant="outline">Add documents</Button>
                    </div>
                  </Card>
                ))}
              </div>
            </TabsContent>
          </Tabs>
        </div>
      </main>

      <Sheet open={panelOpen} onOpenChange={setPanelOpen}>
        <SheetContent side="right" className="sm:max-w-xl">
          <SheetHeader>
            <SheetTitle>Document details</SheetTitle>
          </SheetHeader>
          <ScrollArea className="mt-4 h-full">
            {activeDoc ? (
              <div className="space-y-4 pb-8">
                <div className="space-y-1">
                  <div className="text-xs uppercase tracking-wide text-muted-foreground">{activeDoc.id}</div>
                  <div className="text-lg font-semibold leading-tight">{activeDoc.name}</div>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Badge variant="secondary" className="capitalize">
                    {documentTypeLabel(activeDoc.type)}
                  </Badge>
                  <Badge variant="outline">{activeDoc.size}</Badge>
                  <Badge variant="outline" className="text-xs">
                    {activeDoc.classification}
                  </Badge>
                  <Badge variant="outline">Owner: {activeDoc.owner}</Badge>
                </div>
                <p className="text-sm text-muted-foreground leading-relaxed">{activeDoc.summary}</p>
                <Separator />
                <div className="space-y-2">
                  <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Tags</div>
                  <div className="flex flex-wrap gap-1.5">
                    {activeDoc.tags.map((tag) => (
                      <Badge key={tag} variant="outline" className="text-xs">
                        {tag}
                      </Badge>
                    ))}
                  </div>
                </div>
                <div className="space-y-2">
                  <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Collections</div>
                  <div className="flex flex-wrap gap-1.5">
                    {activeDoc.collections.map((col) => (
                      <Badge key={col} variant="secondary" className="text-xs">
                        {col}
                      </Badge>
                    ))}
                  </div>
                </div>
                <div className="space-y-2">
                  <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Status</div>
                  <div className="inline-flex items-center gap-2 text-xs">
                    <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-1 font-medium", statusBadgeClasses(activeDoc.status))}>
                      {activeDoc.status === "ready" && <CheckCircle2 className="w-3 h-3" />}
                      {activeDoc.status === "processing" && <RefreshCw className="w-3 h-3 animate-spin" />}
                      {activeDoc.status === "error" && <AlertTriangle className="w-3 h-3" />}
                      <span className="capitalize">{activeDoc.status}</span>
                    </span>
                  </div>
                </div>
                <Separator />
                <div className="grid gap-2">
                  <Button className="gap-2">
                    <FileText className="w-4 h-4" /> View full document
                  </Button>
                  <Button variant="outline" className="gap-2">
                    <Layers className="w-4 h-4" /> Manage collections
                  </Button>
                </div>
              </div>
            ) : (
              <div className="text-sm text-muted-foreground">Select a document from the table to preview it here.</div>
            )}
          </ScrollArea>
        </SheetContent>
      </Sheet>
    </div>
  );
};

export default Knowledge;

