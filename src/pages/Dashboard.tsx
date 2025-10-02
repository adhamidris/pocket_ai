import React from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { CalendarDays, ChevronDown, Search, Building2, AlertTriangle, ChevronRight, TrendingUp, Clock, Star, MessageCircle, Bot, Users, Sun, Moon } from "lucide-react";
import { DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Card } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Link } from "react-router-dom";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useI18n } from "@/i18n/I18nProvider";
import LanguageToggle from "@/components/LanguageToggle";
import { useThemeMode } from "@/contexts/ThemeProvider";

const Sidebar = () => {
  const items = [
    { label: "Home", to: "/dashboard", active: true },
    { label: "Cases", to: "/dashboard/cases" },
    { label: "Leads", to: "/dashboard/leads" },
    { label: "Customers", to: "/dashboard/customers" },
    { label: "Agents", to: "/dashboard/agents" },
    { label: "Knowledge", to: "/dashboard/knowledge" },
  ];
  return (
    <aside className="hidden md:flex w-56 shrink-0 border-r border-border/70 bg-card/60 backdrop-blur-sm min-h-screen sticky top-0">
      <div className="flex flex-col w-full p-3 gap-2">
        <div className="px-2 py-3 text-lg font-semibold">Dashboard <span className="text-xs align-top text-muted-foreground">v0.1</span></div>
        <nav className="mt-1 flex-1 space-y-1">
          {items.map(it => (
            <a key={it.label} href={it.to} className={`w-full block text-left px-3 py-2 rounded-md text-sm flex items-center justify-between ${it.active ? 'bg-primary/10 text-primary' : 'hover:bg-muted/60'}`}>
              <span>{it.label}</span>
            </a>
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

const StatCard = ({ icon, title, value, delta, good }: { icon: React.ReactNode; title: string; value: string; delta?: string; good?: boolean; }) => (
  <div className="flex-1 min-w-[180px] rounded-xl border border-border bg-card shadow-sm p-4">
    <div className="flex items-center gap-3">
      <div className="h-10 w-10 rounded-full bg-primary/10 text-primary grid place-items-center">
        {icon}
      </div>
      <div>
        <div className="text-xs text-muted-foreground">{title}</div>
        <div className="text-xl font-semibold mt-0.5">{value}</div>
        {delta && (
          <div className={`text-xs mt-0.5 ${good ? 'text-emerald-600' : 'text-rose-600'}`}>{delta}</div>
        )}
      </div>
    </div>
  </div>
);

const Dashboard = () => {
  const { t } = useI18n();
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const { isLight, toggle } = useThemeMode();
  React.useEffect(() => {
    const id = setTimeout(() => setLoading(false), 400);
    return () => clearTimeout(id);
  }, []);
  
  const recentActivity = [
    { id: '1', type: 'conversation', title: 'Sarah Johnson started a conversation', description: 'Billing inquiry - Urgent priority', timestamp: '5 minutes ago', status: 'urgent' },
    { id: '2', type: 'agent', title: 'Nancy AI resolved 3 conversations', description: 'Average response time: 1.1s', timestamp: '1 hour ago', status: 'success' },
    { id: '3', type: 'customer', title: 'New customer: Michael Chen', description: 'Technical integration support', timestamp: '2 hours ago', status: 'info' },
    { id: '4', type: 'satisfaction', title: 'Emma Rodriguez rated 5 stars', description: 'Resolution: Dashboard access issue', timestamp: '3 hours ago', status: 'success' },
  ] as const

  const getActivityIcon = (type: string) => {
    switch (type) {
      case 'conversation':
        return MessageCircle
      case 'agent':
        return Bot
      case 'customer':
        return Users
      case 'satisfaction':
        return Star
      default:
        return MessageCircle
    }
  }

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'urgent':
        return 'bg-amber-500 text-amber-600 dark:text-amber-300'
      case 'success':
        return 'bg-emerald-500 text-emerald-600 dark:text-emerald-300'
      case 'info':
        return 'bg-primary text-primary'
      default:
        return 'bg-muted-foreground text-muted-foreground'
    }
  }

  return (
    <div className="min-h-screen bg-background flex">
      <Sidebar />
      <main className="flex-1 overflow-auto">
        <div className="w-full px-4 md:px-6 lg:px-8 py-6">
          <section>
            <div className="mb-4">
              <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <div className="text-xl md:text-2xl font-semibold">Hello Evano <span className="align-middle">👋🏻</span></div>
                <div className="flex flex-col md:flex-row md:items-center gap-2 md:gap-3 w-full md:w-auto">
                  {/* Org selector */}
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="secondary" className="inline-flex items-center gap-2">
                        <Building2 className="w-4 h-4" />
                        <span className="font-medium">Pocket AI</span>
                        <ChevronDown className="w-4 h-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="start">
                      <DropdownMenuCheckboxItem checked>Pocket AI</DropdownMenuCheckboxItem>
                      <DropdownMenuCheckboxItem>Acme Inc</DropdownMenuCheckboxItem>
                      <DropdownMenuCheckboxItem>Globex</DropdownMenuCheckboxItem>
                    </DropdownMenuContent>
                  </DropdownMenu>

                  {/* Date range */}
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="secondary" className="inline-flex items-center gap-2">
                        <CalendarDays className="w-4 h-4" />
                        <span className="font-medium">Last 7 days</span>
                        <ChevronDown className="w-4 h-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="start">
                      <DropdownMenuCheckboxItem checked>Last 7 days</DropdownMenuCheckboxItem>
                      <DropdownMenuCheckboxItem>Last 30 days</DropdownMenuCheckboxItem>
                      <DropdownMenuCheckboxItem>Last 90 days</DropdownMenuCheckboxItem>
                      <DropdownMenuCheckboxItem>Custom…</DropdownMenuCheckboxItem>
                    </DropdownMenuContent>
                  </DropdownMenu>

                  {/* Search */}
                  <div className="relative w-full md:w-64">
                    <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                    <Input placeholder="Search…" className="pl-9 bg-input border-0" />
                  </div>

                  {/* Language toggle */}
                  <LanguageToggle />

                  {/* Theme toggle */}
                  <button
                    onClick={toggle}
                    className="p-2 rounded-lg border border-border hover:bg-accent/40 transition-colors"
                    aria-label={t("nav.toggleLightMode")}
                    title={t("nav.toggleLightMode")}
                  >
                    {isLight ? <Moon className="w-5 h-5" /> : <Sun className="w-5 h-5" />}
                  </button>

                  {/* Avatar */}
                  <Avatar className="h-8 w-8">
                    <AvatarImage src="https://api.dicebear.com/7.x/avataaars/svg?seed=adham" />
                    <AvatarFallback>AD</AvatarFallback>
                  </Avatar>
                </div>
              </div>
            </div>

            {/* Urgent banner */}
            <div className="mb-4">
              <Card className="relative overflow-hidden border border-border bg-card">
                <div className="absolute left-0 top-0 bottom-0 w-1.5 bg-amber-500" />
                <div className="p-4 md:p-5 flex items-start gap-3">
                  <div className="h-9 w-9 rounded-full bg-amber-500/10 text-amber-600 dark:text-amber-300 grid place-items-center">
                    <AlertTriangle className="w-5 h-5" />
                  </div>
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="inline-flex items-center gap-1 text-[11px] font-medium text-amber-700 dark:text-amber-300">
                        <span className="h-2 w-2 rounded-full bg-amber-500 animate-pulse" />
                        {t<string>('dashboard.urgent.label')}
                      </span>
                    </div>
                    <div className="text-sm text-muted-foreground">{t<string>('dashboard.urgent.noUrgent')}</div>
                  </div>
                  <Button variant="ghost" className="gap-1 text-amber-700 dark:text-amber-300 hover:bg-amber-500/10">
                    {t<string>('dashboard.urgent.review')}
                    <ChevronRight className="w-4 h-4" />
                  </Button>
                </div>
              </Card>
            </div>

            {/* Stat cards */}
            <div className="rounded-2xl bg-card/60 border border-border p-4 md:p-5 mb-6">
              {error && (
                <Alert className="mb-3" variant="destructive">
                  <AlertDescription>{error}</AlertDescription>
                </Alert>
              )}
              {loading ? (
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                  {[...Array(4)].map((_, i) => (
                    <div key={i} className="flex-1 min-w-[180px] rounded-xl border border-border bg-card shadow-sm p-4">
                      <div className="flex items-center gap-3">
                        <Skeleton className="h-10 w-10 rounded-full" />
                        <div className="flex-1">
                          <Skeleton className="h-3 w-28 mb-2" />
                          <Skeleton className="h-5 w-16" />
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                  <StatCard icon={<TrendingUp className="w-4 h-4" />} title={t<string>('dashboard.stats.todayMessages')} value="43" delta={"+12% vs last week"} good />
                  <StatCard icon={<Clock className="w-4 h-4" />} title={t<string>('dashboard.stats.responseTime')} value="1.2s" delta={"45% faster"} good />
                  <StatCard icon={<Star className="w-4 h-4" />} title={t<string>('dashboard.stats.satisfaction')} value="92%" delta={"+3% this month"} good />
                  <StatCard icon={<MessageCircle className="w-4 h-4" />} title={t<string>('dashboard.stats.activeConversations')} value="7" delta={"+2 vs last hour"} good />
                </div>
              )}
            </div>

            {/* Quick Actions + Recent Activity grid */}
            <div className="grid grid-cols-12 gap-4 mb-6">
              {/* Quick Actions (side rail) */}
              <div className="col-span-12 lg:col-span-4">
                <div className="grid grid-cols-1 gap-4">
                  <Link to="/dashboard/cases" className="group">
                    <Card className="p-4 border-border hover:border-primary/30 transition-colors">
                      <div className="flex items-center gap-3">
                        <div className="h-10 w-10 rounded-full bg-primary/10 text-primary grid place-items-center">
                          <MessageCircle className="w-5 h-5" />
                        </div>
                        <div className="flex-1">
                          <div className="text-sm font-semibold">Cases</div>
                          <div className="text-xs text-muted-foreground">2 active, 1 waiting</div>
                        </div>
                        <ChevronRight className="w-4 h-4 text-muted-foreground group-hover:text-foreground" />
                      </div>
                    </Card>
                  </Link>
                  <Link to="/dashboard/agents" className="group">
                    <Card className="p-4 border-border hover:border-primary/30 transition-colors">
                      <div className="flex items-center gap-3">
                        <div className="h-10 w-10 rounded-full bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 grid place-items-center">
                          <Bot className="w-5 h-5" />
                        </div>
                        <div className="flex-1">
                          <div className="text-sm font-semibold">Agents</div>
                          <div className="text-xs text-muted-foreground">Configure AI assistants</div>
                        </div>
                        <ChevronRight className="w-4 h-4 text-muted-foreground group-hover:text-foreground" />
                      </div>
                    </Card>
                  </Link>
                  <Link to="/dashboard/customers" className="group">
                    <Card className="p-4 border-border hover:border-primary/30 transition-colors">
                      <div className="flex items-center gap-3">
                        <div className="h-10 w-10 rounded-full bg-amber-500/10 text-amber-600 dark:text-amber-400 grid place-items-center">
                          <Users className="w-5 h-5" />
                        </div>
                        <div className="flex-1">
                          <div className="text-sm font-semibold">Customers</div>
                          <div className="text-xs text-muted-foreground">4 customers, 1 VIP</div>
                        </div>
                        <ChevronRight className="w-4 h-4 text-muted-foreground group-hover:text-foreground" />
                      </div>
                    </Card>
                  </Link>
                </div>
              </div>
              {/* Recent Activity (main) */}
              <div className="col-span-12 lg:col-span-8">
                <Card className="border border-border">
                  <div className="p-4 md:p-5 flex items-center justify-between">
                <div className="text-lg font-semibold">{t<string>('dashboard.recentActivity')}</div>
                <Link to="/dashboard/cases" className="text-sm text-primary">{t<string>('dashboard.viewAll')}</Link>
                  </div>
                  <div className="px-4 md:px-5 pb-4">
                    <Tabs defaultValue="all">
                      <TabsList>
                        <TabsTrigger value="all">All</TabsTrigger>
                        <TabsTrigger value="conversation">Conversations</TabsTrigger>
                        <TabsTrigger value="agent">Agents</TabsTrigger>
                        <TabsTrigger value="customer">Customers</TabsTrigger>
                        <TabsTrigger value="satisfaction">Satisfaction</TabsTrigger>
                      </TabsList>
                      {['all','conversation','agent','customer','satisfaction'].map((tab) => (
                        <TabsContent key={tab} value={tab}>
                      <ScrollArea className="h-64">
                            <div className="py-3">
                          {loading ? (
                            <div className="space-y-3">
                              {[...Array(4)].map((_, i) => (
                                <div key={i} className="flex items-start gap-3">
                                  <Skeleton className="h-9 w-9 rounded-full" />
                                  <div className="flex-1 min-w-0">
                                    <Skeleton className="h-4 w-48 mb-2" />
                                    <Skeleton className="h-3 w-64" />
                                  </div>
                                  <Skeleton className="h-3 w-16" />
                                </div>
                              ))}
                            </div>
                          ) : recentActivity
                                .filter((a) => tab === 'all' ? true : a.type === tab)
                                .map((a, idx, arr) => {
                                  const Icon = getActivityIcon(a.type)
                                  const color = getStatusColor(a.status)
                                  return (
                                    <div key={a.id}>
                                      <div className="flex items-start gap-3 py-3">
                                        <div className={`h-9 w-9 rounded-full grid place-items-center border border-border/60 ${a.status === 'urgent' ? 'bg-amber-500/10' : a.status === 'success' ? 'bg-emerald-500/10' : 'bg-primary/10'}`}>
                                          <Icon className={`${a.status === 'urgent' ? 'text-amber-600 dark:text-amber-300' : a.status === 'success' ? 'text-emerald-600 dark:text-emerald-300' : 'text-primary'} w-4 h-4`} />
                                        </div>
                                        <div className="flex-1 min-w-0">
                                          <div className="flex items-center gap-2">
                                            {a.status === 'urgent' && (
                                              <span className="inline-flex items-center gap-1 text-[11px] font-medium text-amber-700 dark:text-amber-300">
                                                <span className="h-1.5 w-1.5 rounded-full bg-amber-500 animate-pulse" />
                                                Urgent
                                              </span>
                                            )}
                                            <div className="text-sm font-medium truncate">{a.title}</div>
                                          </div>
                                          <div className="text-xs text-muted-foreground mt-0.5 truncate">{a.description}</div>
                                        </div>
                                        <div className="text-[11px] text-muted-foreground whitespace-nowrap">{a.timestamp}</div>
                                      </div>
                                  {idx < arr.length - 1 && <Separator />}
                                    </div>
                                  )
                            })}
                          {!loading && recentActivity.filter((a) => tab === 'all' ? true : a.type === tab).length === 0 && (
                            <div className="text-sm text-muted-foreground py-8 text-center">{t<string>('dashboard.emptyFeed')}</div>
                          )}
                            </div>
                          </ScrollArea>
                        </TabsContent>
                      ))}
                    </Tabs>
                  </div>
                </Card>
              </div>
            </div>

            
          </section>
        </div>
      </main>
    </div>
  );
};

export default Dashboard;
