import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";

const Sidebar = () => {
  const items = [
    { label: "Home", to: "/dashboard" },
    { label: "Conversations", to: "/dashboard/conversations", active: true },
    { label: "Leads", to: "/dashboard/leads" },
    { label: "Customers", to: "/dashboard/customers" },
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

const Conversations = () => {
  return (
    <div className="min-h-screen bg-background flex">
      <Sidebar />
      <main className="flex-1 overflow-auto">
        <div className="w-full px-4 md:px-6 lg:px-8 py-6">
          <section>
            <div className="mb-2">
              <div className="text-xl md:text-2xl font-semibold">Conversations</div>
            </div>
            <p className="text-sm text-muted-foreground">This is a placeholder page for conversations.</p>
          </section>
        </div>
      </main>
    </div>
  );
};

export default Conversations;


