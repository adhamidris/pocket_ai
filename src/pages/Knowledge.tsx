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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Pagination, PaginationContent, PaginationItem, PaginationLink, PaginationNext, PaginationPrevious } from "@/components/ui/pagination";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
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
import { ApiError } from "@/services/http";
import {
  startGoogleDriveOAuth,
  fetchGoogleDriveResources,
  fetchIntegrations,
  saveGoogleDriveResources,
  syncGoogleDriveNow,
  type GoogleSheetResource,
  type GoogleResourcesResponse,
  type IntegrationSummary,
  type IntegrationProvider,
} from "@/services/integrations";
import { formatDistanceToNow } from "date-fns";

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

type SheetEditState = {
  selected: boolean;
  visibility: string;
  syncFrequency: string;
  internalOnlyColumns: string;
  excludedColumns: string;
};

type WizardStepId = "connect" | "select" | "privacy" | "done";

const columnListToString = (values?: string[]) => (values && values.length ? values.join(", ") : "");
const columnInputToList = (value: string) =>
  value
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);

const PROVIDER_META: Record<string, { icon: React.ReactNode; description: string; scope?: string }> = {
  google_drive: {
    icon: <Layers className="w-5 h-5 text-primary" />,
    description: "Sync folders containing policies, playbooks, and spreadsheets.",
    scope: "Customer Success › SOPs",
  },
  notion: {
    icon: <FileText className="w-5 h-5 text-primary" />,
    description: "Import knowledge bases, wiki pages, and product docs.",
    scope: "Team workspace › Support KB",
  },
  sharepoint: {
    icon: <ShieldCheck className="w-5 h-5 text-primary" />,
    description: "Centralize compliance and legal templates.",
  },
  custom_api: {
    icon: <Plug className="w-5 h-5 text-primary" />,
    description: "Bring proprietary knowledge sources via JSON schema.",
    scope: "Knowledge Hub API v2",
  },
  excel: {
    icon: <FileSpreadsheet className="w-5 h-5 text-primary" />,
    description: "Automate ingestion of live operational spreadsheets.",
  },
  s3: {
    icon: <Database className="w-5 h-5 text-primary" />,
    description: "Ingest archived PDF manuals and product catalogs.",
  },
};

const integrationDemoSeeds: IntegrationSummary[] = [
  {
    id: "int-google-demo",
    name: "Google Drive",
    type: "google_drive",
    status: "connected",
    lastSyncedAt: new Date(Date.now() - 14 * 60 * 1000).toISOString(),
    resourceCount: 3,
    syncError: "",
    defaultVisibility: "private",
    defaultSyncFrequency: "daily",
    hasCredentials: true,
    actions: {},
    account: { email: "cx-ops@example.com", name: "CX Ops" },
  },
  {
    id: "int-notion-demo",
    name: "Notion",
    type: "notion",
    status: "beta",
    lastSyncedAt: undefined,
    resourceCount: 2,
    syncError: "",
    defaultVisibility: "internal",
    defaultSyncFrequency: "weekly",
    hasCredentials: true,
    actions: {},
    account: { email: "workspace@example.com", name: "Team Workspace" },
  },
  {
    id: "int-sharepoint-demo",
    name: "SharePoint",
    type: "sharepoint",
    status: "disconnected",
    lastSyncedAt: undefined,
    resourceCount: 0,
    syncError: "",
    defaultVisibility: "private",
    defaultSyncFrequency: "weekly",
    hasCredentials: false,
    actions: {},
    account: null,
  },
];

const VISIBILITY_OPTIONS = [
  { value: "private", label: "Private" },
  { value: "internal", label: "Internal" },
  { value: "shared", label: "Shared" },
];

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

const StatBadge = ({ label, value, icon }: { label: string; value: string; icon: React.ReactNode }) => (
  <Card className="relative flex flex-col gap-2 border border-border/60 bg-card/70 p-4 transition-colors hover:border-primary/35">
    <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-primary/35 via-primary/20 to-transparent" />
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

const FieldLabel = ({ label, tooltip }: { label: string; tooltip?: string }) => (
  <div className="text-[11px] uppercase text-muted-foreground font-semibold flex items-center gap-1">
    {label}
    {tooltip ? (
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <button type="button" className="text-muted-foreground hover:text-foreground">
              <HelpCircle className="w-3 h-3" />
            </button>
          </TooltipTrigger>
          <TooltipContent className="max-w-xs text-xs">{tooltip}</TooltipContent>
        </Tooltip>
      </TooltipProvider>
    ) : null}
  </div>
);

const Knowledge = () => {
  const [tab, setTab] = React.useState<"documents" | "integrations" | "collections">("documents");
  const [documents] = React.useState<KnowledgeDocument[]>(documentSeeds);
  const [integrations, setIntegrations] = React.useState<IntegrationSummary[]>(integrationDemoSeeds);
  const [providers, setProviders] = React.useState<IntegrationProvider[]>([]);
  const [collections] = React.useState<KnowledgeCollection[]>(collectionSeeds);
  const [businessId, setBusinessId] = React.useState<string | null>(null);
  const [googleIntegrationId, setGoogleIntegrationId] = React.useState<string | null>(null);
  const [isLoadingIntegrations, setIsLoadingIntegrations] = React.useState(true);
  const [integrationsError, setIntegrationsError] = React.useState<string | null>(null);
  const [search, setSearch] = React.useState("");
  const [activeDoc, setActiveDoc] = React.useState<KnowledgeDocument | null>(documentSeeds[0] || null);
  const [panelOpen, setPanelOpen] = React.useState(false);
  const [isNarrow, setIsNarrow] = React.useState(false);
  const [isConnectingGoogle, setIsConnectingGoogle] = React.useState(false);
  const [connectError, setConnectError] = React.useState<string | null>(null);
  const [googleResources, setGoogleResources] = React.useState<GoogleResourcesResponse | null>(null);
  const [sheetForm, setSheetForm] = React.useState<Record<string, SheetEditState>>({});
  const [isLoadingSheets, setIsLoadingSheets] = React.useState(false);
  const [sheetError, setSheetError] = React.useState<string | null>(null);
  const [sheetSuccess, setSheetSuccess] = React.useState<string | null>(null);
  const [isSavingSheets, setIsSavingSheets] = React.useState(false);
  const [refreshSheetsVersion, setRefreshSheetsVersion] = React.useState(0);
  const [syncNowMessage, setSyncNowMessage] = React.useState<string | null>(null);
  const [syncNowError, setSyncNowError] = React.useState<string | null>(null);
  const [syncingIntegrationId, setSyncingIntegrationId] = React.useState<string | null>(null);
  const providerTiles = React.useMemo<IntegrationProvider[]>(() => {
    if (providers.length) return providers;
    return [
      {
        type: "google_drive",
        label: "Google Drive",
        description: "Sync spreadsheets securely via OAuth.",
        status: "available",
        connectUrl: "",
        requiresOAuth: true,
        supportsSheets: true,
      },
      {
        type: "excel_online",
        label: "Excel Online",
        description: "OneDrive-hosted spreadsheets (coming soon).",
        status: "coming_soon",
        connectUrl: "",
        requiresOAuth: true,
        supportsSheets: true,
      },
    ];
  }, [providers]);

  const formatIntegrationTime = React.useCallback((timestamp?: string) => {
    if (!timestamp) return "Never";
    const parsed = new Date(timestamp);
    if (Number.isNaN(parsed.getTime())) {
      return timestamp;
    }
    return formatDistanceToNow(parsed, { addSuffix: true });
  }, []);

  const loadIntegrations = React.useCallback(() => {
    setIsLoadingIntegrations(true);
    fetchIntegrations()
      .then((data) => {
        setBusinessId(data.businessId);
        setProviders(data.providers || []);
        setIntegrations(data.integrations.length ? data.integrations : integrationDemoSeeds);
        const google = data.integrations.find((entry) => entry.type === "google_drive");
        setGoogleIntegrationId(google?.id ?? null);
        setIntegrationsError(null);
      })
      .catch((error) => {
        if (error instanceof ApiError) {
          setIntegrationsError(error.message || "Unable to load integrations.");
        } else if (error instanceof Error) {
          setIntegrationsError(error.message);
        } else {
          setIntegrationsError("Unable to load integrations.");
        }
        setProviders([]);
        setIntegrations(integrationDemoSeeds);
        setGoogleIntegrationId(null);
      })
      .finally(() => setIsLoadingIntegrations(false));
  }, []);

  React.useEffect(() => {
    loadIntegrations();
  }, [loadIntegrations]);

  React.useEffect(() => {
    const mq = window.matchMedia("(max-width: 1023px)");
    const onChange = () => setIsNarrow(mq.matches);
    mq.addEventListener("change", onChange);
    setIsNarrow(mq.matches);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const buildSheetFormState = React.useCallback((data: GoogleResourcesResponse) => {
    const map: Record<string, SheetEditState> = {};
    const seen = new Set<string>();
    const combined: GoogleSheetResource[] = [];
    data.availableResources.forEach((resource) => {
      combined.push(resource);
      seen.add(resource.resourceId);
    });
    data.selectedResources.forEach((resource) => {
      if (!seen.has(resource.resourceId)) {
        combined.push({ ...resource, selected: true });
        seen.add(resource.resourceId);
      }
    });
    combined.forEach((resource) => {
      map[resource.resourceId] = {
        selected: resource.selected ?? data.selectedResources.some((entry) => entry.resourceId === resource.resourceId),
        visibility: resource.visibility || data.defaultVisibility,
        syncFrequency: resource.syncFrequency || data.defaultSyncFrequency,
        internalOnlyColumns: columnListToString(resource.columnPrivacy?.internalOnlyColumns),
        excludedColumns: columnListToString(resource.columnPrivacy?.excludedColumns),
      };
    });
    return map;
  }, []);

  const refreshGoogleResources = React.useCallback(() => {
    if (!googleIntegrationId) return;
    setRefreshSheetsVersion((prev) => prev + 1);
  }, [googleIntegrationId]);

  React.useEffect(() => {
    if (tab !== "integrations") return;
    if (!googleIntegrationId) {
      setGoogleResources(null);
      setSheetForm({});
      setIsLoadingSheets(false);
      return;
    }
    let cancelled = false;
    setIsLoadingSheets(true);
    fetchGoogleDriveResources({ integrationId: googleIntegrationId, businessId: businessId || undefined })
      .then((data) => {
        if (cancelled) return;
        setGoogleResources(data);
        setSheetForm(buildSheetFormState(data));
        setSheetError(null);
      })
      .catch((error) => {
        if (cancelled) return;
        if (error instanceof ApiError) {
          setSheetError(error.message || "Unable to load Google Sheets.");
        } else if (error instanceof Error) {
          setSheetError(error.message);
        } else {
          setSheetError("Unable to load Google Sheets.");
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoadingSheets(false);
      });
    return () => {
      cancelled = true;
    };
  }, [tab, refreshSheetsVersion, buildSheetFormState, googleIntegrationId, businessId]);

  const handleConnectGoogle = React.useCallback(async () => {
    setConnectError(null);
    setIsConnectingGoogle(true);
    try {
      const response = await startGoogleDriveOAuth(businessId || undefined);
      if (response?.authorizationUrl) {
        window.location.href = response.authorizationUrl;
        return;
      }
      throw new Error("Missing authorization URL from backend.");
    } catch (error) {
      console.error("Failed to initiate Google Drive OAuth", error);
      if (error instanceof ApiError) {
        setConnectError(error.message || "Unable to start Google OAuth.");
      } else if (error instanceof Error) {
        setConnectError(error.message);
      } else {
        setConnectError("Unable to start Google OAuth.");
      }
    } finally {
      setIsConnectingGoogle(false);
    }
  }, [businessId]);

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

  const pageSize = 25;
  const [page, setPage] = React.useState(1);
  React.useEffect(() => setPage(1), [search]);
  const pageCount = React.useMemo(() => Math.max(1, Math.ceil(filteredDocs.length / pageSize)), [filteredDocs.length, pageSize]);
  const pageItems = React.useMemo(() => filteredDocs.slice((page - 1) * pageSize, page * pageSize), [filteredDocs, page, pageSize]);
  React.useEffect(() => {
    setPage((prev) => Math.min(prev, pageCount));
  }, [pageCount]);

  const openDocument = (doc: KnowledgeDocument) => {
    setActiveDoc(doc);
    if (isNarrow) {
      setPanelOpen(true);
    }
  };

  const updateSheetState = React.useCallback(
    (resourceId: string, patch: Partial<SheetEditState>) => {
      setSheetForm((prev) => {
        const base: SheetEditState =
          prev[resourceId] || {
            selected: false,
            visibility: googleResources?.defaultVisibility || "private",
            syncFrequency: googleResources?.defaultSyncFrequency || "daily",
            internalOnlyColumns: "",
            excludedColumns: "",
          };
        return {
          ...prev,
          [resourceId]: { ...base, ...patch },
        };
      });
    },
    [googleResources],
  );

  const findResourceMeta = React.useCallback(
    (resourceId: string) => {
      if (!googleResources) return undefined;
      return (
        googleResources.availableResources.find((resource) => resource.resourceId === resourceId) ||
        googleResources.selectedResources.find((resource) => resource.resourceId === resourceId)
      );
    },
    [googleResources],
  );

  const handleSaveSheetConfig = React.useCallback(async () => {
    if (!googleResources) return;
    setSheetError(null);
    setSheetSuccess(null);
    setIsSavingSheets(true);
    try {
      const selectedEntries = Object.entries(sheetForm).filter(([, state]) => state.selected);
      const payloadResources = selectedEntries
        .map(([resourceId, state]) => {
          const meta = findResourceMeta(resourceId);
          if (!meta) return null;
          return {
            resourceId,
            driveFileId: meta.driveFileId,
            sheetGid: meta.sheetGid,
            sheetName: meta.sheetName,
            driveFileName: meta.driveFileName,
            visibility: state.visibility,
            syncFrequency: state.syncFrequency,
            columnPrivacy: {
              sharedColumns: meta.columnPrivacy?.sharedColumns || [],
              internalOnlyColumns: columnInputToList(state.internalOnlyColumns),
              excludedColumns: columnInputToList(state.excludedColumns),
            },
          };
        })
        .filter((entry): entry is NonNullable<typeof entry> => Boolean(entry));

      await saveGoogleDriveResources(
        googleResources.integration.id,
        {
          resources: payloadResources,
          defaultVisibility: googleResources.defaultVisibility,
          defaultSyncFrequency: googleResources.defaultSyncFrequency,
        },
        { businessId: businessId || undefined },
      );

      setSheetSuccess("Sheet sync settings saved.");
      refreshGoogleResources();
    } catch (error) {
      if (error instanceof ApiError) {
        setSheetError(error.message || "Unable to save sheet configuration.");
      } else if (error instanceof Error) {
        setSheetError(error.message);
      } else {
        setSheetError("Unable to save sheet configuration.");
      }
    } finally {
      setIsSavingSheets(false);
    }
  }, [googleResources, sheetForm, findResourceMeta, refreshGoogleResources, businessId]);

  const selectedSheetCount = React.useMemo(() => Object.values(sheetForm).filter((state) => state.selected).length, [sheetForm]);
  const missingSheetConfigs = React.useMemo(() => {
    if (!googleResources) return [] as GoogleSheetResource[];
    return googleResources.selectedResources.filter(
      (resource) => !googleResources.availableResources.some((available) => available.resourceId === resource.resourceId),
    );
  }, [googleResources]);

  const privacyWarnings = React.useMemo(() => {
    if (!googleResources) return [] as string[];
    return Object.entries(sheetForm)
      .filter(([, state]) => state.selected && !state.internalOnlyColumns && !state.excludedColumns)
      .map(([resourceId]) => resourceId);
  }, [googleResources, sheetForm]);

  const privacyWarningNames = React.useMemo(() => {
    if (!googleResources) return [] as string[];
    return privacyWarnings
      .map((resourceId) => findResourceMeta(resourceId)?.sheetName || resourceId)
      .filter(Boolean);
  }, [privacyWarnings, findResourceMeta, googleResources]);

  const hasConnectedGoogle = React.useMemo(() => {
    if (!googleIntegrationId) return false;
    return integrations.some((entry) => entry.id === googleIntegrationId && entry.hasCredentials);
  }, [googleIntegrationId, integrations]);

  const sheetsSelected = React.useMemo(() => selectedSheetCount > 0, [selectedSheetCount]);
  const privacyReady = sheetsSelected && privacyWarnings.length === 0;
  const wizardSteps = React.useMemo(
    () =>
      (
        [
          {
            id: "connect" as WizardStepId,
            label: "Connect account",
            description: "Authorize Google Drive via OAuth from the dashboard.",
            complete: hasConnectedGoogle,
          },
          {
            id: "select" as WizardStepId,
            label: "Select sheets",
            description: "Pick spreadsheets + tabs that should sync automatically.",
            complete: sheetsSelected,
          },
          {
            id: "privacy" as WizardStepId,
            label: "Review privacy",
            description: "Exclude or mask sensitive columns before ingesting.",
            complete: privacyReady,
          },
          {
            id: "done" as WizardStepId,
            label: "Ready to sync",
            description: "Kick off ingestion or let the scheduler keep things fresh.",
            complete: privacyReady && hasConnectedGoogle,
          },
        ] satisfies Array<{ id: WizardStepId; label: string; description: string; complete: boolean }>
      ),
    [hasConnectedGoogle, sheetsSelected, privacyReady],
  );
  const activeWizardStep = React.useMemo(() => wizardSteps.find((step) => !step.complete)?.id ?? "done", [wizardSteps]);

  const handleSyncNow = React.useCallback(async () => {
    if (!googleIntegrationId) {
      setSyncNowError("Connect Google Drive before triggering a sync.");
      return;
    }
    setSyncNowError(null);
    setSyncNowMessage(null);
    setSyncingIntegrationId(googleIntegrationId);
    try {
      await syncGoogleDriveNow({ integrationId: googleIntegrationId });
      setSyncNowMessage("Sync started. Ingestion jobs queued.");
      refreshGoogleResources();
    } catch (error) {
      if (error instanceof ApiError) {
        setSyncNowError(error.message || "Unable to start sync.");
      } else if (error instanceof Error) {
        setSyncNowError(error.message);
      } else {
        setSyncNowError("Unable to start sync.");
      }
    } finally {
      setSyncingIntegrationId(null);
    }
  }, [googleIntegrationId, refreshGoogleResources]);

  return (
    <div className="min-h-screen bg-background flex">
      <Sidebar />
      <main className="flex-1 overflow-auto">
        <div className="w-full px-4 md:px-6 lg:px-8 py-6">
          <header className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
            <div className="text-xl md:text-2xl font-semibold text-gradient-hero">Knowledge</div>
            <div className="flex flex-wrap items-center gap-2">
              <Button className="gap-2">
                <UploadCloud className="w-4 h-4" /> Upload Doc
              </Button>
              <Button
                variant="outline"
                className="gap-2"
                onClick={handleConnectGoogle}
                disabled={isConnectingGoogle}
              >
                {isConnectingGoogle ? (
                  <RefreshCw className="w-4 h-4 animate-spin" />
                ) : (
                  <LinkIcon className="w-4 h-4" />
                )}
                {isConnectingGoogle ? "Connecting…" : "Connect Google Drive"}
              </Button>
            </div>
          </header>
          {connectError && (
            <div className="mt-2 flex items-center gap-1 text-xs text-destructive">
              <AlertTriangle className="w-3 h-3" />
              <span>{connectError}</span>
            </div>
          )}

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
                <div className="flex flex-wrap items-center gap-2 md:ml-auto">
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
                <div className="flex flex-col gap-2">
                  <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                    <RefreshCw className="w-3.5 h-3.5" />
                    <span>Bulk actions</span>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <Button variant="outline" size="sm" className="gap-2">
                      <RefreshCw className="w-4 h-4" /> Sync all
                    </Button>
                    <Button variant="outline" size="sm" className="gap-1">
                      <CheckCircle2 className="w-4 h-4" /> Show connected only
                    </Button>
                  </div>
                </div>
              )}
              {tab === "collections" && (
                <div className="flex flex-col gap-2">
                  <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                    <Layers className="w-3.5 h-3.5" />
                    <span>Actions</span>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <Button size="sm">Sort by activity</Button>
                    <Button size="sm">New collection</Button>
                  </div>
                </div>
              )}
            </div>

            <TabsContent value="documents" className="mt-5">
              <div className="space-y-4">
                  <div className="space-y-2">
                    <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                      <UploadCloud className="w-3.5 h-3.5" />
                      <span>Add content</span>
                    </div>
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
                  </div>

                  <div className="space-y-2">
                    <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                      <FileText className="w-3.5 h-3.5" />
                      <span>Document library</span>
                    </div>
                    <div className="relative w-full md:w-64">
                      <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                      <Input
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        placeholder="Search documents…"
                        className="pl-9 border border-border/60 bg-muted/70 focus-visible:ring-0 focus-visible:border-border"
                      />
                    </div>
                  </div>
                  <Card className="relative border border-border/70 bg-card shadow-lg shadow-black/5 backdrop-blur hover:border-primary/35 transition-colors dark:border-slate-700/50 dark:bg-slate-800/70 dark:shadow-black/25">
                    <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-primary/35 via-primary/20 to-transparent" />
                    <ScrollArea className="max-h-[70vh]">
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
                          {pageItems.map((doc) => (
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
            </TabsContent>

            <TabsContent value="integrations" className="mt-5">
              <div className="space-y-4">
                <Card className="border border-primary/30 bg-primary/5 p-4 space-y-4">
                  <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-primary">
                    <Plug className="w-3.5 h-3.5" />
                    <span>Connect -> Select -> Review -> Done</span>
                  </div>
                  <div className="grid gap-4 md:grid-cols-4">
                    {wizardSteps.map((step, index) => (
                      <div key={step.id} className="flex items-start gap-3">
                        <div
                          className={cn(
                            "h-8 w-8 rounded-full border grid place-items-center text-xs font-semibold",
                            step.complete
                              ? "border-primary bg-primary text-primary-foreground"
                              : activeWizardStep === step.id
                                ? "border-primary text-primary"
                                : "border-border/60 text-muted-foreground",
                          )}
                        >
                          {step.complete ? <CheckCircle2 className="w-4 h-4" /> : index + 1}
                        </div>
                        <div className="space-y-1">
                          <div className="text-xs font-semibold">{step.label}</div>
                          <p className="text-xs text-muted-foreground leading-relaxed">{step.description}</p>
                        </div>
                      </div>
                    ))}
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <Button size="sm" className="gap-2" onClick={handleConnectGoogle} disabled={isConnectingGoogle}>
                      {isConnectingGoogle ? <RefreshCw className="w-4 h-4 animate-spin" /> : <LinkIcon className="w-4 h-4" />}
                      {isConnectingGoogle ? "Connecting…" : "Connect Google Drive"}
                    </Button>
                    <div className="text-xs text-muted-foreground">
                      Finish each step to keep spreadsheets syncing automatically.
                    </div>
                  </div>
                </Card>

                {integrationsError && (
                  <div className="flex items-center gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-4 py-2 text-xs text-destructive">
                    <AlertTriangle className="w-3 h-3" />
                    <span>{integrationsError}</span>
                    <Button size="sm" variant="ghost" className="h-6 px-2" onClick={loadIntegrations}>
                      Retry
                    </Button>
                  </div>
                )}

                <Card className="border border-border/60 bg-card/80 p-4 space-y-3">
                  <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                    <Layers className="w-3.5 h-3.5" />
                    <span>Available connectors</span>
                  </div>
                  <div className="grid gap-3 md:grid-cols-2">
                    {providerTiles.map((provider) => (
                      <div key={provider.type} className="rounded-lg border border-border/60 bg-background/80 p-3 space-y-2">
                        <div className="flex items-center justify-between">
                          <div className="text-sm font-semibold">{provider.label}</div>
                          <Badge variant="outline" className="text-[11px] capitalize">
                            {provider.status}
                          </Badge>
                        </div>
                        <p className="text-xs text-muted-foreground leading-relaxed">{provider.description}</p>
                        {provider.status === "available" && provider.type === "google_drive" && (
                          <Button size="sm" variant="outline" className="gap-2" onClick={handleConnectGoogle} disabled={isConnectingGoogle}>
                            {isConnectingGoogle ? <RefreshCw className="w-4 h-4 animate-spin" /> : <LinkIcon className="w-4 h-4" />}
                            {isConnectingGoogle ? "Connecting…" : "Launch connect"}
                          </Button>
                        )}
                      </div>
                    ))}
                  </div>
                </Card>

                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                      <Plug className="w-3.5 h-3.5" />
                      <span>Connected sources</span>
                      {isLoadingIntegrations && <span className="text-[10px] uppercase text-primary">Refreshing…</span>}
                    </div>
                    <div className="flex flex-wrap items-center gap-2 text-[11px] uppercase text-muted-foreground">
                      <RefreshCw className="w-3 h-3" />
                      <span>Bulk actions</span>
                      <Button variant="outline" size="sm" className="gap-2" onClick={handleSyncNow} disabled={!googleIntegrationId}>
                        <RefreshCw className="w-4 h-4" /> Sync all
                      </Button>
                      <Button variant="outline" size="sm" className="gap-1">
                        <CheckCircle2 className="w-4 h-4" /> Show connected only
                      </Button>
                    </div>
                  </div>
                  <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-3">
                    {integrations.map((integration) => {
                      const meta = PROVIDER_META[integration.type] || {
                        icon: <Plug className="w-5 h-5 text-primary" />,
                        description: "Custom connector",
                        scope: undefined,
                      };
                      const isGoogle = integration.id === googleIntegrationId;
                      const syncing = isGoogle && syncingIntegrationId === integration.id;
                      const buttonLabel = integration.status === "connected" ? "Sync now" : isGoogle ? "Connect" : "Configure";
                      return (
                        <Card
                          key={integration.id}
                          className={cn(
                            "border border-border/60 bg-card/70 p-4 flex flex-col gap-4",
                            integration.status === "disconnected" && "border-dashed border-primary/40",
                          )}
                        >
                          <div className="flex items-start gap-3">
                            <div className="h-10 w-10 rounded-full bg-primary/10 grid place-items-center">{meta.icon}</div>
                            <div className="flex-1 space-y-1">
                              <div className="flex items-center gap-2">
                                <div className="text-sm font-semibold">{integration.name}</div>
                                <Badge variant="secondary" className="capitalize">
                                  {integration.status}
                                </Badge>
                              </div>
                              <p className="text-xs text-muted-foreground leading-relaxed">{meta.description}</p>
                              {meta.scope && (
                                <div className="inline-flex items-center gap-1 rounded-full border border-border/60 bg-muted/60 px-2 py-1 text-[11px] text-muted-foreground">
                                  <Layers className="w-3 h-3" /> {meta.scope}
                                </div>
                              )}
                              {integration.account?.email && (
                                <div className="text-[11px] text-muted-foreground">
                                  Linked as {integration.account.email}
                                </div>
                              )}
                            </div>
                          </div>
                          <div className="flex items-center justify-between text-xs text-muted-foreground">
                            <span>Last sync</span>
                            <span className="text-foreground font-medium">{formatIntegrationTime(integration.lastSyncedAt)}</span>
                          </div>
                          {integration.syncError && (
                            <div className="flex flex-wrap items-center gap-2 rounded-md border border-destructive/40 bg-destructive/5 px-2 py-2 text-[11px] text-destructive">
                              <AlertTriangle className="w-3 h-3" />
                              <span className="flex-1">Sync failed: {integration.syncError}</span>
                              {isGoogle && (
                                <Button size="sm" variant="ghost" className="h-6 px-2" onClick={handleConnectGoogle}>
                                  Reconnect
                                </Button>
                              )}
                            </div>
                          )}
                          <div className="flex flex-wrap gap-2">
                            <Button
                              size="sm"
                              variant="outline"
                              className="gap-2"
                              onClick={isGoogle ? handleSyncNow : undefined}
                              disabled={isGoogle && (!googleIntegrationId || syncing)}
                            >
                              {syncing ? <RefreshCw className="w-4 h-4 animate-spin" /> : integration.status === "connected" ? <RefreshCw className="w-4 h-4" /> : <Plug className="w-4 h-4" />}
                              {syncing ? "Syncing…" : buttonLabel}
                            </Button>
                            <Button size="sm" variant="ghost" className="text-xs">
                              Configure access
                            </Button>
                          </div>
                        </Card>
                      );
                    })}
                  </div>
                </div>

                <Card className="border border-border/60 bg-card/80 p-4 space-y-4">
                  <div className="flex flex-col gap-1 md:flex-row md:items-center md:justify-between">
                    <div>
                      <div className="text-sm font-semibold">Google Sheets configuration</div>
                      <p className="text-xs text-muted-foreground">
                        Select which spreadsheets to sync and mark sensitive columns.
                      </p>
                    </div>
                    <div className="flex gap-2">
                      <Button variant="outline" size="sm" onClick={refreshGoogleResources} disabled={isLoadingSheets || !googleIntegrationId}>
                        <RefreshCw className={cn("w-4 h-4", isLoadingSheets && "animate-spin")} />
                        Refresh list
                      </Button>
                      <Button
                        size="sm"
                        onClick={handleSyncNow}
                        disabled={!googleIntegrationId || syncingIntegrationId === googleIntegrationId}
                        className="gap-2"
                      >
                        {syncingIntegrationId === googleIntegrationId ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Plug className="w-4 h-4" />}
                        Sync now
                      </Button>
                    </div>
                  </div>
                  {sheetError && (
                    <div className="text-xs text-destructive flex items-center gap-1">
                      <AlertTriangle className="w-3 h-3" />
                      <span>{sheetError}</span>
                    </div>
                  )}
                  {sheetSuccess && (
                    <div className="text-xs text-emerald-600 flex items-center gap-1">
                      <CheckCircle2 className="w-3 h-3" />
                      <span>{sheetSuccess}</span>
                    </div>
                  )}
                  {syncNowError && (
                    <div className="text-xs text-destructive flex items-center gap-1">
                      <AlertTriangle className="w-3 h-3" />
                      <span>{syncNowError}</span>
                    </div>
                  )}
                  {syncNowMessage && (
                    <div className="text-xs text-emerald-600 flex items-center gap-1">
                      <CheckCircle2 className="w-3 h-3" />
                      <span>{syncNowMessage}</span>
                    </div>
                  )}
                  {!googleIntegrationId ? (
                    <p className="text-xs text-muted-foreground">Connect Google Drive to configure sheet syncing.</p>
                  ) : isLoadingSheets && !googleResources ? (
                    <p className="text-xs text-muted-foreground">Loading Google Sheets…</p>
                  ) : googleResources ? (
                    <div className="space-y-4">
                      {privacyWarnings.length > 0 && (
                        <div className="flex gap-2 rounded-md border border-amber-300 bg-amber-50 p-3 text-[11px] text-amber-900">
                          <AlertTriangle className="w-3 h-3" />
                          <div>
                            <div className="font-semibold">Review privacy controls</div>
                            <p>
                              {privacyWarningNames.join(", ") || "Selected sheets"} currently sync all columns. Mark sensitive fields as
                              internal-only or excluded to avoid accidental sharing.
                            </p>
                          </div>
                        </div>
                      )}
                      {missingSheetConfigs.length > 0 && (
                        <div className="text-[11px] text-muted-foreground">
                          {missingSheetConfigs.length} previously selected sheet{missingSheetConfigs.length > 1 ? "s" : ""} no longer appear in
                          Drive. Confirm the file still exists or remove it from sync.
                        </div>
                      )}
                      {googleResources.availableResources.length === 0 ? (
                        <p className="text-xs text-muted-foreground">No spreadsheets detected for the connected Google account.</p>
                      ) : (
                        googleResources.availableResources.map((resource) => {
                          const state = sheetForm[resource.resourceId] || {
                            selected: false,
                            visibility: googleResources.defaultVisibility,
                            syncFrequency: googleResources.defaultSyncFrequency,
                            internalOnlyColumns: "",
                            excludedColumns: "",
                          };
                          return (
                            <div key={resource.resourceId} className="rounded-lg border border-border/60 bg-background/60 p-3 space-y-3">
                              <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
                                <div>
                                  <div className="text-sm font-semibold">{resource.driveFileName}</div>
                                  <div className="text-xs text-muted-foreground">Tab · {resource.sheetName}</div>
                                  <div className="text-[11px] text-muted-foreground mt-1">
                                    {resource.rowCount ?? "?"} rows · {resource.columnCount ?? "?"} columns
                                  </div>
                                </div>
                                <div className="flex items-center gap-2">
                                  <Switch
                                    checked={state.selected}
                                    onCheckedChange={(checked) => updateSheetState(resource.resourceId, { selected: checked })}
                                  />
                                  <span className="text-xs text-muted-foreground">
                                    {state.selected ? "Synced" : "Not synced"}
                                  </span>
                                </div>
                              </div>
                              <div className="grid gap-3 md:grid-cols-2">
                                <div className="space-y-1">
                                  <FieldLabel label="Visibility" tooltip="Controls the default knowledge visibility for this sheet." />
                                  <Select
                                    value={state.visibility}
                                    onValueChange={(value) => updateSheetState(resource.resourceId, { visibility: value })}
                                  >
                                    <SelectTrigger className="h-9">
                                      <SelectValue placeholder="Select visibility" />
                                    </SelectTrigger>
                                    <SelectContent>
                                      {VISIBILITY_OPTIONS.map((option) => (
                                        <SelectItem key={option.value} value={option.value}>
                                          {option.label}
                                        </SelectItem>
                                      ))}
                                    </SelectContent>
                                  </Select>
                                </div>
                                <div className="space-y-1">
                                  <FieldLabel
                                    label="Internal-only columns"
                                    tooltip="These columns stay visible to trusted teammates but are hidden from external users."
                                  />
                                  <Textarea
                                    rows={2}
                                    value={state.internalOnlyColumns}
                                    onChange={(event) =>
                                      updateSheetState(resource.resourceId, { internalOnlyColumns: event.target.value })
                                    }
                                    placeholder="Comma-separated column names"
                                  />
                                </div>
                                <div className="space-y-1">
                                  <FieldLabel
                                    label="Exclude from sync"
                                    tooltip="Columns listed here never leave the spreadsheet. Useful for PII or one-off notes."
                                  />
                                  <Textarea
                                    rows={2}
                                    value={state.excludedColumns}
                                    onChange={(event) =>
                                      updateSheetState(resource.resourceId, { excludedColumns: event.target.value })
                                    }
                                    placeholder="Comma-separated column names"
                                  />
                                </div>
                                <div className="space-y-1">
                                  <FieldLabel label="Last sync" />
                                  <div className="text-xs text-muted-foreground">
                                    {resource.lastSyncedAt ? (
                                      <>
                                        <Clock className="inline-block w-3 h-3 mr-1" />
                                        {resource.lastSyncedAt}
                                      </>
                                    ) : (
                                      "Not yet synced"
                                    )}
                                  </div>
                                </div>
                              </div>
                            </div>
                          );
                        })
                      )}
                    </div>
                  ) : (
                    <div className="text-xs text-muted-foreground">Connect Google Drive to configure sheet syncing.</div>
                  )}
                  <div className="flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      className="gap-2"
                      onClick={handleSaveSheetConfig}
                      disabled={
                        !googleIntegrationId || isSavingSheets || isLoadingSheets || !googleResources?.availableResources.length
                      }
                    >
                      {isSavingSheets ? <RefreshCw className="w-4 h-4 animate-spin" /> : <CheckCircle2 className="w-4 h-4" />}
                      {isSavingSheets ? "Saving…" : "Save sheet settings"}
                    </Button>
                  </div>
                </Card>
              </div>
            </TabsContent>

            <TabsContent value="collections" className="mt-5">
              <div className="space-y-2">
                <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                  <Layers className="w-3.5 h-3.5" />
                  <span>Collections</span>
                </div>
                <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-3">
                {collections.map((collection) => (
                  <Card key={collection.id} className="relative border border-border/60 bg-card/70 p-4 flex flex-col gap-4 transition-colors hover:border-primary/35">
                    <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-primary/35 via-primary/20 to-transparent" />
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
