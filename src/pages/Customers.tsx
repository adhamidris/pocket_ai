import React, { useEffect } from "react";
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
import { Search, ChevronDown, EllipsisVertical, Mail, MessageCircle, Phone, Filter, UserPlus } from "lucide-react";

type Channel = "email" | "chat" | "whatsapp" | "phone";
type Status = "Active" | "Inactive";
type Customer = {
  id: string;
  name: string;
  email: string;
  avatar?: string;
  channel: Channel;
  status: Status;
  conversations: number;
  satisfaction: number; // 0-100
  lastContact: string; // ISO
  totalValue: number; // USD
  owner: string;
  vip?: boolean;
  tags: string[];
  joinedAt: string; // ISO
};

const channelBadge = (ch: Channel) => {
  if (ch === "email") return { label: "EM", className: "bg-primary/10 text-primary" };
  if (ch === "chat") return { label: "WEB", className: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-300" };
  if (ch === "whatsapp") return { label: "WA", className: "bg-green-500/10 text-green-600 dark:text-green-300" };
  return { label: "PH", className: "bg-amber-500/10 text-amber-700 dark:text-amber-300" };
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

const maskEmail = (email: string) => {
  const [user, domain] = email.split("@");
  const maskedUser = user.length <= 3 ? user[0] + "**" : user.slice(0, 3) + "***";
  return `${maskedUser}@${domain}`;
};

const useDebounced = (val: string, delay = 300) => {
  const [d, setD] = React.useState(val);
  React.useEffect(() => { const id = setTimeout(() => setD(val), delay); return () => clearTimeout(id); }, [val, delay]);
  return d;
};

const Customers = () => {
  const [items, setItems] = React.useState<Customer[]>(seed);
  const [panelOpen, setPanelOpen] = React.useState(false);
  const [active, setActive] = React.useState<Customer | null>(null);
  const [query, setQuery] = React.useState("");
  const q = useDebounced(query, 300);
  const [status, setStatus] = React.useState<Status | "All">("All");
  const [channels, setChannels] = React.useState<Set<Channel>>(new Set());
  const [tags, setTags] = React.useState<Set<string>>(new Set());
  const [openFilters, setOpenFilters] = React.useState(false);
  const [joinedRange, setJoinedRange] = React.useState<{ from?: Date; to?: Date }>({});
  const [valueRange, setValueRange] = React.useState<[number, number]>([0, 20000]);
  const [selected, setSelected] = React.useState<Set<string>>(new Set());
  const [density, setDensity] = React.useState<"compact" | "comfortable">("compact");
  const rowH = density === "compact" ? "h-11" : "h-13";
  const [pageSize, setPageSize] = React.useState<number>(() => Number(localStorage.getItem("customers.pageSize") || 25));
  const [page, setPage] = React.useState(1);
  const [isNarrow, setIsNarrow] = React.useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 1023px)");
    const onChange = () => setIsNarrow(mq.matches);
    mq.addEventListener("change", onChange);
    setIsNarrow(mq.matches);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const toggleTag = (t: string) => setTags((prev) => { const n = new Set(prev); n.has(t) ? n.delete(t) : n.add(t); return n; });
  const toggleChannel = (c: Channel) => setChannels((prev) => { const n = new Set(prev); n.has(c) ? n.delete(c) : n.add(c); return n; });

  const filtered = React.useMemo(() => {
    let arr = [...items];
    if (status !== "All") arr = arr.filter((i) => i.status === status);
    if (channels.size) arr = arr.filter((i) => channels.has(i.channel));
    if (tags.size) arr = arr.filter((i) => i.tags.some((t) => tags.has(t)));
    if (q) {
      const needle = q.toLowerCase();
      arr = arr.filter((i) => i.name.toLowerCase().includes(needle) || i.email.toLowerCase().includes(needle) || i.tags.join(" ").toLowerCase().includes(needle));
    }
    if (joinedRange.from && joinedRange.to) {
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
                          <Calendar mode="range" selected={joinedRange as any} onSelect={(r: any) => setJoinedRange({ from: r?.from, to: r?.to })} numberOfMonths={2} />
                        </div>
                        <div>
                          <div className="text-sm font-medium mb-2">Value range</div>
                          <div className="text-xs text-muted-foreground mb-2">${valueRange[0].toLocaleString()} – ${valueRange[1].toLocaleString()}</div>
                          <Slider min={0} max={25000} step={100} value={valueRange as any} onValueChange={(v: any) => setValueRange([v[0], v[1]])} />
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
                <Table>
                  <TableHeader className="sticky top-[52px] bg-card/90 backdrop-blur z-10">
                    <TableRow className="hover:bg-transparent">
                      <TableHead className="w-8">
                        <Checkbox checked={allSelectedOnPage} onCheckedChange={toggleSelectAllOnPage} aria-label="Select all" className={someSelectedOnPage ? "data-[state=indeterminate]:opacity-100" : ""} />
                      </TableHead>
                      <TableHead className="min-w-[220px]">Customer</TableHead>
                      <TableHead>Email</TableHead>
                      <TableHead>Channel</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Conversations</TableHead>
                      <TableHead>Satisfaction</TableHead>
                      <TableHead>Last Contact</TableHead>
                      <TableHead>Total Value</TableHead>
                      <TableHead>Owner</TableHead>
                      <TableHead className="w-8" />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {pageItems.length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={11}>
                          <div className="p-6 text-sm text-muted-foreground flex items-center justify-between">
                            <div>No customers match your filters.</div>
                            {(status !== 'All' || channels.size || tags.size || q || (joinedRange.from && joinedRange.to) || valueRange[0] !== 0 || valueRange[1] !== 20000) && (
                              <Button variant="ghost" size="sm" onClick={() => { setStatus('All'); setChannels(new Set()); setTags(new Set()); setQuery(''); setJoinedRange({}); setValueRange([0,20000]); }}>
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
                            <Checkbox checked={selected.has(c.id)} onCheckedChange={(v) => setSelected((prev) => { const n = new Set(prev); v ? n.add(c.id) : n.delete(c.id); return n; })} />
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
                                  {c.vip && <Badge className="bg-amber-500/20 text-amber-600 dark:text-amber-300 border-amber-500/30">VIP</Badge>}
                                </div>
                                <div className="text-xs text-muted-foreground" title={'Email hidden'}>{maskEmail(c.email)}</div>
                                <div className="mt-1 flex items-center gap-1 flex-wrap">
                                  {c.tags.map((t) => (
                                    <Badge key={t} variant="secondary" className="text-[10px]">{t}</Badge>
                                  ))}
                                </div>
                              </div>
                            </div>
                          </TableCell>
                          <TableCell className="text-sm">{maskEmail(c.email)}</TableCell>
                          <TableCell>
                            <span className={`text-xs px-2 py-1 rounded ${channelBadge(c.channel).className}`}>{channelBadge(c.channel).label}</span>
                          </TableCell>
                          <TableCell>
                            <Badge variant={c.status === 'Active' ? 'secondary' : 'outline'}>{c.status}</Badge>
                          </TableCell>
                          <TableCell>{c.conversations}</TableCell>
                          <TableCell>{c.satisfaction}%</TableCell>
                          <TableCell>{formatRelative(c.lastContact)}</TableCell>
                          <TableCell>${c.totalValue.toLocaleString()}</TableCell>
                          <TableCell>{c.owner}</TableCell>
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
              </Card>
            </div>

            {/* Pagination */}
            <div className="sticky bottom-0 z-10 border-t border-border/60 bg-background/95 backdrop-blur mt-3 py-2">
              <div className="flex items-center justify-between px-2">
                <div className="text-xs text-muted-foreground">{total} customers • Page {page} of {pageCount}</div>
                <div className="flex items-center gap-2">
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="outline" size="sm">{pageSize}/page</Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      {[10,25,50,100].map((n) => (
                        <DropdownMenuItem key={n} onClick={() => { setPageSize(n); localStorage.setItem('customers.pageSize', String(n)); }}>
                          Show {n}
                        </DropdownMenuItem>
                      ))}
                    </DropdownMenuContent>
                  </DropdownMenu>
                  <Pagination>
                    <PaginationContent>
                      <PaginationItem>
                        <PaginationPrevious href="#" onClick={() => setPage((p) => Math.max(1, p-1))} />
                      </PaginationItem>
                      {Array.from({ length: pageCount }).slice(0, 5).map((_, i) => (
                        <PaginationItem key={i}>
                          <PaginationLink href="#" isActive={page === i+1} onClick={() => setPage(i+1)}>{i+1}</PaginationLink>
                        </PaginationItem>
                      ))}
                      <PaginationItem>
                        <PaginationNext href="#" onClick={() => setPage((p) => Math.min(pageCount, p+1))} />
                      </PaginationItem>
                    </PaginationContent>
                  </Pagination>
                </div>
              </div>
            </div>
          </section>
        </div>
      </main>

      {/* Right Side Panel */}
      <Sheet open={panelOpen} onOpenChange={setPanelOpen}>
        <SheetContent side="right" className={`${isNarrow ? 'w-screen' : 'w-[92vw] sm:w-[520px]'} p-0`}>
          <div className="h-full flex flex-col">
            <div className="px-4 py-3 border-b border-border/60 bg-card/80 backdrop-blur">
              <SheetHeader>
                <SheetTitle className="flex items-center gap-2">
                  <Avatar className="h-8 w-8">
                    {active?.avatar && <AvatarImage src={active.avatar} />}
                    <AvatarFallback>{active?.name?.slice(0,2).toUpperCase()}</AvatarFallback>
                  </Avatar>
                  <span className="font-semibold">{active?.name}</span>
                  {active?.vip && <Badge className="bg-amber-500/20 text-amber-600 dark:text-amber-300 border-amber-500/30">VIP</Badge>}
                </SheetTitle>
              </SheetHeader>
            </div>

            <Tabs defaultValue="overview" className="flex-1 flex flex-col">
              <div className="px-4 pt-2">
                <TabsList className="grid grid-cols-4 w-full">
                  <TabsTrigger value="overview">Overview</TabsTrigger>
                  <TabsTrigger value="cases">Cases</TabsTrigger>
                  <TabsTrigger value="activity">Activity</TabsTrigger>
                  <TabsTrigger value="notes">Notes</TabsTrigger>
                </TabsList>
              </div>
              <div className="flex-1 min-h-0 p-4 space-y-4">
                <TabsContent value="overview">
                  <Card className="p-3">
                    <div className="grid grid-cols-2 gap-3 text-sm">
                      <div>
                        <div className="text-xs text-muted-foreground">Email</div>
                        <div>{active?.email}</div>
                      </div>
                      <div>
                        <div className="text-xs text-muted-foreground">Owner</div>
                        <div>{active?.owner}</div>
                      </div>
                      <div>
                        <div className="text-xs text-muted-foreground">Status</div>
                        <div>{active?.status}</div>
                      </div>
                      <div>
                        <div className="text-xs text-muted-foreground">Joined</div>
                        <div>{active ? new Date(active.joinedAt).toLocaleDateString() : "—"}</div>
                      </div>
                    </div>
                    <Separator className="my-3" />
                    <div className="text-xs text-muted-foreground mb-1">Tags</div>
                    <div className="flex flex-wrap gap-2">
                      {active?.tags?.length ? active.tags.map((t) => <Badge key={t} variant="secondary">{t}</Badge>) : <div className="text-sm">No tags.</div>}
                    </div>
                  </Card>
                </TabsContent>

                <TabsContent value="cases">
                  <Card className="p-3">
                    <div className="text-sm font-semibold mb-2">Recent Cases</div>
                    <div className="space-y-2 text-sm">
                      <div>No cases yet — Create a case from this panel.</div>
                    </div>
                  </Card>
                </TabsContent>

                <TabsContent value="activity">
                  <Card className="p-3">
                    <div className="text-sm font-semibold mb-2">Activity (last 180 days)</div>
                    <div className="text-sm text-muted-foreground">No activity to show.</div>
                  </Card>
                </TabsContent>

                <TabsContent value="notes">
                  <Card className="p-3">
                    <div className="text-sm font-semibold mb-2">Notes</div>
                    <Input placeholder="Add a quick note…" />
                  </Card>
                </TabsContent>
              </div>
            </Tabs>

            <div className="px-4 py-3 border-t border-border/60 flex items-center justify-end gap-2">
              <Button variant="outline">Email</Button>
              <Button>Start Case</Button>
            </div>
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
};

export default Customers;
