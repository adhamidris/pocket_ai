export type Priority = "low" | "medium" | "high" | "urgent";
export type Status = "open" | "resolved";
export type Channel = "email" | "chat" | "whatsapp" | "phone";
export type Category = "Inquiry" | "Request" | "Complaint";

export type CaseRecord = {
  id: string;
  customer: { name: string; email: string; avatar?: string };
  title: string;
  snippet: string;
  priority: Priority;
  status: Status;
  channel: Channel;
  assignee?: { name: string; avatar?: string };
  startedAt: string; // ISO
  unread: number;
  category: Category;
  documents?: { id: string; name: string; size: string; type: string }[];
  history?: { id: string; at: string; actor: string; action: string }[];
};

export type ListParams = {
  status: Status;
  categories?: Category[];
  q?: string;
  from?: string;
  to?: string;
  page?: number;
  pageSize?: number;
  sort?: { field: "startedAt" | "priority" | "unread"; dir: "asc" | "desc" };
};

export type ListResponse = {
  items: CaseRecord[];
  total: number;
  page: number;
  pageSize: number;
};

const base: CaseRecord[] = [
  {
    id: "C-1042",
    customer: { name: "Sarah Johnson", email: "sarah.johnson@gmail.com", avatar: "https://api.dicebear.com/7.x/avataaars/svg?seed=Sarah" },
    title: "Billing inquiry",
    snippet: "I was charged twice for last month...",
    priority: "urgent",
    status: "open",
    channel: "email",
    assignee: { name: "Nancy AI", avatar: "https://api.dicebear.com/7.x/bottts/svg?seed=Nancy" },
    startedAt: new Date(Date.now() - 1000 * 60 * 35).toISOString(),
    unread: 2,
    category: "Inquiry",
    documents: [
      { id: "d1", name: "invoice-aug.pdf", size: "234 KB", type: "pdf" },
      { id: "d2", name: "screenshot.png", size: "1.2 MB", type: "image" },
    ],
    history: [
      { id: "h1", at: new Date(Date.now() - 1000 * 60 * 32).toISOString(), actor: "Nancy AI", action: "Replied with billing policy" },
      { id: "h2", at: new Date(Date.now() - 1000 * 60 * 30).toISOString(), actor: "System", action: "Marked as urgent" },
    ],
  },
  {
    id: "C-1038",
    customer: { name: "Michael Chen", email: "michael.chen@acme.com", avatar: "https://api.dicebear.com/7.x/avataaars/svg?seed=Michael" },
    title: "Integration help",
    snippet: "Having trouble with OAuth callback...",
    priority: "high",
    status: "open",
    channel: "chat",
    assignee: { name: "Alex Rivera" },
    startedAt: new Date(Date.now() - 1000 * 60 * 90).toISOString(),
    unread: 0,
    category: "Request",
  },
  {
    id: "C-1032",
    customer: { name: "Emma Rodriguez", email: "emma.rodriguez@outlook.com" },
    title: "Access issue",
    snippet: "Can't access dashboard from mobile...",
    priority: "medium",
    status: "resolved",
    channel: "phone",
    assignee: { name: "Nancy AI" },
    startedAt: new Date(Date.now() - 1000 * 60 * 60 * 26).toISOString(),
    unread: 0,
    category: "Complaint",
  },
  {
    id: "C-1024",
    customer: { name: "David Kim", email: "d.kim@globex.com" },
    title: "Feature request",
    snippet: "Can we get export CSV?",
    priority: "low",
    status: "open",
    channel: "email",
    assignee: { name: "—" },
    startedAt: new Date(Date.now() - 1000 * 60 * 60 * 72).toISOString(),
    unread: 1,
    category: "Request",
  },
];

// Expand base to simulate larger dataset
const db: CaseRecord[] = (() => {
  const arr: CaseRecord[] = [...base];
  const names = ["Ava Patel", "Noah Lee", "Liam Davis", "Olivia Brown", "Isabella Garcia", "Ethan Wilson"];
  const domains = ["example.com", "mail.com", "company.io"]; 
  const titles = ["Login issue", "Payment failure", "Account upgrade", "Bug report", "Feature request", "General inquiry"];
  const snippets = ["Can't sign in since yesterday", "Card declined despite funds", "Need to change plan", "Error on step 2", "CSV export needed", "How does billing work?"];
  for (let i = 200; i < 250; i++) {
    const name = names[i % names.length];
    const email = `${name.toLowerCase().replace(/\s/g, ".")}@${domains[i % domains.length]}`;
    const status: Status = i % 5 === 0 ? "resolved" : "open";
    const priority: Priority = (['low','medium','high','urgent'] as Priority[])[i % 4];
    const channel: Channel = (['email','chat','phone','whatsapp'] as Channel[])[i % 4];
    arr.push({
      id: `C-${i}`,
      customer: { name, email },
      title: titles[i % titles.length],
      snippet: snippets[i % snippets.length],
      priority,
      status,
      channel,
      assignee: { name: i % 3 === 0 ? 'Nancy AI' : '—' },
      startedAt: new Date(Date.now() - 1000 * 60 * (i * 9)).toISOString(),
      unread: i % 3,
      category: (['Inquiry','Request','Complaint'] as Category[])[i % 3],
    });
  }
  return arr;
})();

export async function listCases(params: ListParams): Promise<ListResponse> {
  // Simulate network latency
  await new Promise((res) => setTimeout(res, 200));
  const { status, categories = [], q = "", from, to, page = 1, pageSize = 25, sort = { field: "startedAt", dir: "desc" } } = params;

  let arr = db.filter((c) => c.status === status);
  if (categories.length) arr = arr.filter((c) => categories.includes(c.category));
  if (q) {
    const needle = q.toLowerCase();
    arr = arr.filter(
      (c) =>
        c.title.toLowerCase().includes(needle) ||
        c.snippet.toLowerCase().includes(needle) ||
        c.customer.name.toLowerCase().includes(needle) ||
        c.customer.email.toLowerCase().includes(needle)
    );
  }
  if (from && to) {
    const fromMs = new Date(from).getTime();
    const toMs = new Date(to).getTime();
    arr = arr.filter((c) => {
      const d = new Date(c.startedAt).getTime();
      return d >= fromMs && d <= toMs;
    });
  }
  // Sort
  arr.sort((a, b) => {
    const f = sort.field;
    let A: number | string = (a as any)[f];
    let B: number | string = (b as any)[f];
    if (f === "startedAt") {
      A = new Date(a.startedAt).getTime();
      B = new Date(b.startedAt).getTime();
    }
    const cmp = A < B ? -1 : A > B ? 1 : 0;
    return sort.dir === "asc" ? cmp : -cmp;
  });

  const total = arr.length;
  const start = (page - 1) * pageSize;
  const items = arr.slice(start, start + pageSize);

  return { items, total, page, pageSize };
}

