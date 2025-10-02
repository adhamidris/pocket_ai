import React from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import LanguageToggle from "@/components/LanguageToggle";
import { cn } from "@/lib/utils";
import {
  AlertTriangle,
  Bell,
  BookOpen,
  Building2,
  CalendarDays,
  CheckCircle2,
  ChevronDown,
  Clock,
  Eye,
  Flame,
  Globe,
  Mail,
  MessageCircle,
  Moon,
  Phone,
  Rocket,
  Sparkle,
  Star,
  Sun,
  Target,
  TrendingUp,
  Users,
} from "lucide-react";
import { useThemeMode } from "@/contexts/ThemeProvider";

type HandlerType = "ai" | "human";

type ConversationItem = {
  id: string;
  title: string;
  summary: string;
  timestamp: string;
  handledBy: HandlerType;
  channel: "email" | "chat" | "voice";
  priority?: "urgent" | "normal";
};

type ConversationGroup = {
  label: string;
  items: ConversationItem[];
};

const conversationGroups: ConversationGroup[] = [
  {
    label: "Today",
    items: [
      {
        id: "conv-1",
        title: "Billing escalation resolved by Nancy AI",
        summary: "Duplicate invoice identified and refunded automatically.",
        timestamp: "7 min ago",
        handledBy: "ai",
        channel: "chat",
        priority: "urgent",
      },
      {
        id: "conv-2",
        title: "Marcus Lee replied with procurement checklist",
        summary: "Awaiting security review before contract signature.",
        timestamp: "32 min ago",
        handledBy: "human",
        channel: "email",
      },
      {
        id: "conv-3",
        title: "Onboarding walkthrough scheduled",
        summary: "AI assistant booked kickoff for Skyline Retail.",
        timestamp: "1h ago",
        handledBy: "ai",
        channel: "chat",
      },
    ],
  },
  {
    label: "Yesterday",
    items: [
      {
        id: "conv-4",
        title: "Support satisfaction survey",
        summary: "Emma Rodriguez rated 5 stars for instant resolution.",
        timestamp: "18h ago",
        handledBy: "ai",
        channel: "email",
      },
      {
        id: "conv-5",
        title: "Legal compliance inquiry",
        summary: "Assigned to Alex Rivera for manual follow-up.",
        timestamp: "22h ago",
        handledBy: "human",
        channel: "email",
      },
    ],
  },
];

const CHANNEL_BADGES: Record<ConversationItem["channel"], { label: string; color: string; icon: React.ComponentType<{ className?: string }> }> = {
  email: { label: "Email", color: "text-sky-400", icon: Mail },
  chat: { label: "Chat", color: "text-emerald-400", icon: MessageCircle },
  voice: { label: "Voice", color: "text-violet-400", icon: Phone },
};

const slaTrendHours = [1.4, 1.2, 1.6, 1.1, 1.0, 1.3, 0.9];

const aiPulseMetrics = [
  { label: "Total cases", value: "312", delta: "↑ 6% vs last week", tone: "neutral" },
  { label: "Resolved cases", value: "287", delta: "92% resolution rate", tone: "positive" },
  { label: "Escalations", value: "12", delta: "↓ 18% escalations", tone: "positive" },
];

const teamSnapshot = [
  {
    id: "agent-1",
    name: "Nancy AI",
    role: "Support Specialist",
    score: "CSAT 92%",
    avatar: "https://api.dicebear.com/7.x/avataaars/svg?seed=Nancy",
  },
  {
    id: "agent-2",
    name: "Atlas AI",
    role: "Billing Analyst",
    score: "SLA 96%",
    avatar: "https://api.dicebear.com/7.x/avataaars/svg?seed=Atlas",
  },
  {
    id: "agent-3",
    name: "Helix AI",
    role: "Onboarding Coach",
    score: "NPS 54",
    avatar: "https://api.dicebear.com/7.x/avataaars/svg?seed=Helix",
  },
];


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
    <aside className="hidden md:flex w-56 shrink-0 border-r border-border/70 bg-card/60 backdrop-blur-sm min-h-screen sticky top-0 sidebar-card-chrome">
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
    delta: string;
  }) => {
    const tone = React.useMemo(() => {
      if (/\-|↓/.test(delta)) return "text-rose-500";
      if (/risk|over|miss/.test(delta.toLowerCase())) return "text-amber-500";
      return "text-primary";
    }, [delta]);

    return (
      <Card className="relative overflow-hidden border border-border/40 bg-card/80 backdrop-blur-sm transition-all duration-300 hover:border-primary/35">
        <div className="absolute inset-0 bg-gradient-to-br from-primary/15 via-transparent to-transparent opacity-60" />
        <div className="absolute inset-x-0 top-0 h-[1px] bg-gradient-to-r from-transparent via-primary/40 to-transparent" />
        <div className="relative p-5 space-y-3">
          <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/70">{label}</span>
          <div className="text-3xl font-semibold tracking-tight text-foreground">{value}</div>
          <div className={cn("text-xs font-medium", tone)}>{delta}</div>
        </div>
      </Card>
    );
  }
);

const dottedSeparator = "border-t border-dashed border-border/40";

const Home2 = () => {
  const [loading, setLoading] = React.useState(true);
  const { isLight, toggle } = useThemeMode();
  const [tab, setTab] = React.useState<HandlerType | "all">("all");
  const urgentCount = 1;
  const [hoveredId, setHoveredId] = React.useState<string | null>(null);

  React.useEffect(() => {
    const timer = setTimeout(() => setLoading(false), 450);
    return () => clearTimeout(timer);
  }, []);

  

  const filteredGroups = React.useMemo(() => {
    if (tab === "all") return conversationGroups;
    return conversationGroups.map((group) => ({
      ...group,
      items: group.items.filter((item) => item.handledBy === tab),
    })).filter((group) => group.items.length);
  }, [tab]);

  const maxSla = Math.max(...slaTrendHours);

  return (
    <div className="min-h-screen bg-background flex">
      <Sidebar />
      <main className="flex-1 overflow-auto">
        <div className="w-full px-4 md:px-6 lg:px-8 py-6">
          <section className="flex flex-col gap-6">
            <div className="flex flex-col gap-6">
              <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div className="space-y-2">
              <div className="text-2xl md:text-3xl font-semibold tracking-tight">Pocket AI Command Center</div>
            </div>
            <div className="flex flex-col gap-2 md:items-end">
              <div className="flex flex-wrap items-center gap-2 justify-start md:justify-end">
                <Button variant="ghost" size="icon" className="h-9 w-9 rounded-full border border-border/60 hover:bg-accent/40">
                  <Bell className="w-4 h-4 text-muted-foreground" />
                </Button>
                <LanguageToggle />
                <button
                  onClick={toggle}
                  className="h-9 w-9 inline-flex items-center justify-center rounded-full border border-border/60 hover:bg-accent/40 transition-colors"
                  aria-label="Toggle theme"
                >
                  {isLight ? <Moon className="w-4 h-4" /> : <Sun className="w-4 h-4" />}
                </button>
              </div>
            </div>
          </div>

              <Card className="relative overflow-hidden border border-amber-200/50 bg-card/70 backdrop-blur">
                <div className="absolute inset-0 bg-gradient-to-r from-amber-200/40 via-amber-100/5 to-transparent" />
                <div className="relative px-4 py-3 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                  <div className="flex items-start gap-3">
                    <div className="h-9 w-9 rounded-xl bg-white/80 text-amber-600 shadow-[0_6px_18px_-12px_rgba(245,158,11,0.6)] grid place-items-center">
                      <AlertTriangle className="w-4 h-4" />
                    </div>
                    <div className="space-y-1">
                      <div className="text-xs font-semibold uppercase tracking-[0.14em] text-amber-600">Escalation monitor</div>
                      <div className="text-sm font-semibold text-foreground">
                        {urgentCount > 0 ? `${urgentCount} escalation needs a human follow-up` : "All clear—next check in 2h"}
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 md:ml-6">
                    <Button size="sm" className="rounded-full px-4 bg-amber-500 text-white hover:bg-amber-500/90">
                      Review now
                    </Button>
                  </div>
                </div>
              </Card>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {loading ? (
                Array.from({ length: 3 }).map((_, idx) => (
                  <Card key={`stat-skel-${idx}`} className="border border-border/40 bg-card/55 backdrop-blur-sm p-5">
                    <div className="space-y-4">
                      <div className="space-y-1">
                        <Skeleton className="h-2 w-16" />
                        <Skeleton className="h-7 w-24" />
                        <Skeleton className="h-3 w-20" />
                      </div>
                      <div className="h-16 flex items-center px-4">
                        <Skeleton className="h-[2px] w-full" />
                      </div>
                      <Skeleton className="h-3 w-32" />
                    </div>
                  </Card>
                ))
              ) : (
                <>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <div>
                        <StatCard
                          label="Live conversations"
                          value="58"
                          delta="↑ 9% vs last week"
                        />
                      </div>
                    </TooltipTrigger>
                    <TooltipContent align="center">Includes AI-led + human-assisted sessions.</TooltipContent>
                  </Tooltip>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <div>
                        <StatCard
                          label="Avg. resolution time"
                          value="1.2h"
                          delta="SLA 98% met"
                        />
                      </div>
                    </TooltipTrigger>
                    <TooltipContent align="center">Weighted by lead and customer segments.</TooltipContent>
                  </Tooltip>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <div>
                        <StatCard
                          label="Customer satisfaction"
                          value="94%"
                          delta="+3 pts this month"
                        />
                      </div>
                    </TooltipTrigger>
                    <TooltipContent align="center">Post-resolution surveys + proactive feedback.</TooltipContent>
                  </Tooltip>
                </>
              )}
            </div>

            <div className="grid grid-cols-12 gap-4">
              <div className="col-span-12 lg:col-span-8 space-y-4">
                <Card className="border border-border/40 bg-card/70 backdrop-blur-md">
                  <div className="flex flex-col gap-4 p-5">
                    <div className="flex flex-col gap-2">
                      <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-3">
                        <div className="space-y-1">
                          <div className="flex items-center gap-2">
                            <div className="text-base font-semibold">Conversation radar</div>
                            <Badge className="rounded-full bg-primary/10 text-primary text-[10px] uppercase tracking-[0.12em]">
                              Live
                            </Badge>
                          </div>
                          <p className="text-xs text-muted-foreground">Unified feed across AI automations and human specialists.</p>
                        </div>
                        <Tabs value={tab} onValueChange={(value) => setTab(value as typeof tab)} className="self-end">
                          <TabsList className="bg-muted/30">
                            {(["all", "ai", "human"] as const).map((value) => {
                              const count = conversationGroups
                                .flatMap((group) => group.items)
                                .filter((item) => (value === "all" ? true : item.handledBy === value)).length;
                              return (
                                <TabsTrigger key={value} value={value} className="text-xs">
                                  <span className="flex items-center gap-1">
                                    {value.charAt(0).toUpperCase() + value.slice(1)}
                                    <span className="text-[10px] text-muted-foreground/80">{count}</span>
                                  </span>
                                </TabsTrigger>
                              );
                            })}
                          </TabsList>
                        </Tabs>
                      </div>
                    </div>
                    <Separator className="bg-border/40" />
                    {loading ? (
                      <div className="space-y-3">
                        {Array.from({ length: 4 }).map((_, idx) => (
                          <div key={`conv-skel-${idx}`} className="flex items-start gap-3">
                            <Skeleton className="h-9 w-9 rounded-full" />
                            <div className="flex-1 space-y-2">
                              <Skeleton className="h-3 w-32" />
                              <Skeleton className="h-3 w-52" />
                            </div>
                            <Skeleton className="h-3 w-16" />
                          </div>
                        ))}
                      </div>
                    ) : filteredGroups.length ? (
                      <div className="space-y-5">
                        {filteredGroups.map((group) => {
                          const channelCounts = group.items.reduce(
                    (acc, item) => {
                      acc[item.channel] = (acc[item.channel] || 0) + 1;
                      return acc;
                    },
                    {} as Record<ConversationItem["channel"], number>
                          );
                          return (
                            <div key={group.label} className="space-y-3">
                              <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.1em] text-muted-foreground">
                                <span>{group.label}</span>
                                <span className="text-muted-foreground/60">·</span>
                                <span className="rounded-full bg-muted/40 px-2 py-0.5 text-[10px] text-muted-foreground/90">
                                  {group.items.length} updates
                                </span>
                              </div>
                              <div className="space-y-3">
                                {group.items.map((item, index) => {
                                  const channelBadge = CHANNEL_BADGES[item.channel];
                                  const isHovered = hoveredId === item.id;
                                  return (
                                    <React.Fragment key={item.id}>
                                      <div
                                        className={cn(
                                          "flex items-start gap-3 rounded-xl border border-transparent px-2 py-2 transition",
                                          isHovered && "border-border/60 bg-muted/40"
                                        )}
                                        onMouseEnter={() => setHoveredId(item.id)}
                                        onMouseLeave={() => setHoveredId(null)}
                                      >
                                        <div
                                          className={cn(
                                            "h-9 w-9 rounded-full grid place-items-center border border-border/50",
                                            item.handledBy === "ai" ? "bg-primary/10 text-primary" : "bg-muted/50 text-muted-foreground"
                                          )}
                                        >
                                          {item.handledBy === "ai" ? <Sparkle className="w-4 h-4" /> : <Users className="w-4 h-4" />}
                                        </div>
                                        <div className="flex-1 min-w-0 space-y-1">
                                          <div className="flex flex-wrap items-center gap-2">
                                            <div className="text-sm font-medium truncate">{item.title}</div>
                                            {item.priority === "urgent" && (
                                              <Badge className="h-5 px-2 text-[9px] bg-rose-500/15 text-rose-300 border border-rose-500/30">Urgent</Badge>
                                            )}
                                            <Badge
                                              variant="outline"
                                              className={cn(
                                                "h-5 px-2 inline-flex items-center gap-1 rounded-full border-border/60 bg-background text-[10px] text-muted-foreground",
                                                channelBadge.color
                                              )}
                                            >
                                              {React.createElement(channelBadge.icon, { className: "w-3 h-3" })}
                                              <span>{channelBadge.label}</span>
                                            </Badge>
                                          </div>
                                          <div className="text-xs text-muted-foreground truncate">{item.summary}</div>
                                        </div>
                                        <div className="flex items-center gap-3 text-[11px] text-muted-foreground whitespace-nowrap">
                                          <span>{item.timestamp}</span>
                                          <Button size="sm" variant="ghost" className="h-7 gap-1 text-xs text-muted-foreground hover:text-primary">
                                            <Eye className="w-3.5 h-3.5" />
                                            View
                                          </Button>
                                        </div>
                                      </div>
                                      {index < group.items.length - 1 && <div className={dottedSeparator} />}
                                    </React.Fragment>
                                  );
                                })}
                              </div>
                              <div className="h-px" />
                            </div>
                          );
                        })}
                      </div>
                    ) : (
                      <div className="py-10 text-sm text-muted-foreground text-center">
                        Nothing to show for this filter yet.
                      </div>
                    )}
                  </div>
                </Card>
              </div>

              <div className="col-span-12 lg:col-span-4 space-y-4">
                <Card className="border border-border/40 bg-card/70 backdrop-blur-md">
                  <div className="p-5 space-y-4">
                    <div className="text-base font-semibold">AI pulse</div>
                    <div className="space-y-3">
                      {aiPulseMetrics.map((item, idx) => (
                        <div key={item.label} className="flex items-center gap-3">
                          <div className="w-5 flex justify-center">
                            {idx === 0 ? (
                              <Target className="w-4 h-4 text-primary" />
                            ) : idx === 1 ? (
                              <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                            ) : (
                              <AlertTriangle className="w-4 h-4 text-rose-400" />
                            )}
                          </div>
                          <div className="flex-1 min-w-0">
                            <div className="text-sm font-medium">{item.value}</div>
                            <div className="text-xs text-muted-foreground truncate">{item.label}</div>
                          </div>
                          <div className="text-xs text-primary font-medium whitespace-nowrap">{item.delta}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                </Card>

                <Card className="border border-border/40 bg-card/70 backdrop-blur-md">
                  <div className="p-5 space-y-4">
                    <div className="text-base font-semibold">Agent snapshot</div>
                    <div className="space-y-3">
                      {teamSnapshot.map((member) => (
                        <div key={member.id} className="flex items-center justify-between gap-3">
                          <div className="flex items-center gap-3">
                            <Avatar className="h-9 w-9">
                              <AvatarImage src={member.avatar} alt={member.name} />
                              <AvatarFallback>{member.name.slice(0, 2)}</AvatarFallback>
                            </Avatar>
                            <div>
                              <div className="text-sm font-medium">{member.name}</div>
                              <div className="text-xs text-muted-foreground">{member.role}</div>
                            </div>
                          </div>
                          <Button variant="outline" size="sm" className="rounded-full text-xs px-3">
                            {member.score}
                          </Button>
                        </div>
                      ))}
                    </div>
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

const SearchIcon = ({ className }: { className?: string }) => <svg className={cn("text-current", className)} fill="none" height="16" width="16" viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg"><path d="M7.333 12.667a5.333 5.333 0 1 0 0-10.667 5.333 5.333 0 0 0 0 10.667ZM14 14l-2.9-2.9" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.333" /></svg>;

const ShieldIcon = ({ className }: { className?: string }) => <svg className={cn("text-current", className)} fill="none" height="16" width="16" viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg"><path d="M3.333 3.333 8 1.333l4.667 2v4.534c0 1.7-.777 3.29-2.1 4.342L8 14.667l-2.567-2.458c-1.323-1.052-2.1-2.642-2.1-4.342V3.333Z" stroke="currentColor" strokeLinejoin="round" strokeWidth="1.333" /></svg>;

export default Home2;

