import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { ChevronDown, Search } from "lucide-react";
import { DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";

const Sidebar = () => {
  const items = [
    { label: "Dashboard" },
    { label: "Product" },
    { label: "Customers", active: true },
    { label: "Income" },
    { label: "Promote" },
    { label: "Help" },
  ];
  return (
    <aside className="hidden md:flex w-56 shrink-0 border-r border-border/70 bg-card/60 backdrop-blur-sm min-h-screen sticky top-0">
      <div className="flex flex-col w-full p-3 gap-2">
        <div className="px-2 py-3 text-lg font-semibold">Dashboard <span className="text-xs align-top text-muted-foreground">v0.1</span></div>
        <nav className="mt-1 flex-1 space-y-1">
          {items.map(it => (
            <button key={it.label} className={`w-full text-left px-3 py-2 rounded-md text-sm flex items-center justify-between ${it.active ? 'bg-primary/10 text-primary' : 'hover:bg-muted/60'}`}>
              <span>{it.label}</span>
            </button>
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
  return (
    <div className="min-h-screen bg-background flex">
      <Sidebar />
      <main className="flex-1 overflow-auto">
        <div className="w-full px-4 md:px-6 lg:px-8 py-6">
          <section>
            <div className="mb-4">
              <div className="text-xl md:text-2xl font-semibold">Hello Evano <span className="align-middle">👋🏻</span></div>
            </div>

            {/* Stat cards */}
            <div className="rounded-2xl bg-card/60 border border-border p-4 md:p-5 mb-6">
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                <StatCard icon={<span className="text-lg">👥</span>} title="Total Customers" value="—" delta={"+16% this month"} good />
                <StatCard icon={<span className="text-lg">🧑‍🤝‍🧑</span>} title="Members" value="—" delta={"-1% this month"} />
                <StatCard icon={<span className="text-lg">🖥️</span>} title="Active Now" value="—" />
                <StatCard icon={<span className="text-lg">📈</span>} title="Sessions" value="—" />
              </div>
            </div>

            {/* All Customers */}
            <div className="rounded-2xl border border-border bg-card">
              <div className="p-4 md:p-5 border-b border-border/70">
                <div className="text-lg font-semibold">All Customers</div>
                <div className="mt-1 text-sm text-primary cursor-pointer">Active Members</div>
              </div>
              <div className="p-4 md:p-5">
                <div className="flex flex-col md:flex-row md:items-center gap-3 md:gap-4 mb-4">
                  <div className="relative w-full md:max-w-sm">
                    <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                    <Input placeholder="Search" className="pl-9 bg-input border-0" />
                  </div>
                  <div className="ms-auto">
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="secondary" className="inline-flex items-center gap-2">
                          <span>Sort by:</span>
                          <span className="font-medium">Newest</span>
                          <ChevronDown className="w-4 h-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuCheckboxItem checked>Newest</DropdownMenuCheckboxItem>
                        <DropdownMenuCheckboxItem>Oldest</DropdownMenuCheckboxItem>
                        <DropdownMenuCheckboxItem>Active</DropdownMenuCheckboxItem>
                        <DropdownMenuCheckboxItem>Inactive</DropdownMenuCheckboxItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                </div>

                {/* Table scaffold */}
                <div className="overflow-auto rounded-lg border border-border/70">
                  <table className="w-full text-sm">
                    <thead className="bg-muted/50 text-muted-foreground">
                      <tr>
                        <th className="text-left px-4 py-3 font-medium">Customer Name</th>
                        <th className="text-left px-4 py-3 font-medium">Company</th>
                        <th className="text-left px-4 py-3 font-medium">Phone Number</th>
                        <th className="text-left px-4 py-3 font-medium">Email</th>
                        <th className="text-left px-4 py-3 font-medium">Country</th>
                        <th className="text-left px-4 py-3 font-medium">Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {[...Array(8)].map((_, i) => (
                        <tr key={i} className="border-t border-border/70">
                          <td className="px-4 py-3">—</td>
                          <td className="px-4 py-3">—</td>
                          <td className="px-4 py-3">—</td>
                          <td className="px-4 py-3">—</td>
                          <td className="px-4 py-3">—</td>
                          <td className="px-4 py-3"><Badge variant="secondary">Active</Badge></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                <div className="flex items-center justify-between text-xs text-muted-foreground mt-3">
                  <div>Showing data 1 to 8 of 256k entries</div>
                  <div className="inline-flex items-center gap-1">
                    <Button size="icon" variant="secondary" className="h-7 w-7">&lt;</Button>
                    {[1,2,3,4,5].map(n => (
                      <Button key={n} size="icon" variant={n===1? 'default' : 'secondary'} className={`h-7 w-7 ${n===1? 'bg-primary text-primary-foreground' : ''}`}>{n}</Button>
                    ))}
                    <Button size="icon" variant="secondary" className="h-7 w-7">…</Button>
                    <Button size="icon" variant="secondary" className="h-7 w-7">&gt;</Button>
                  </div>
                </div>
              </div>
            </div>
          </section>
        </div>
      </main>
    </div>
  );
};

export default Dashboard;


