import React, { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import ChatHeader from "@/components/chat/ChatHeader";
import ChatInput from "@/components/chat/ChatInput";
import ChatMessages from "@/components/chat/ChatMessages";
import { Message } from "@/components/chat/ChatMessage";
import { useToast } from "@/hooks/use-toast";
import { Button } from "@/components/ui/button";

// Mock agent data - replace with API call
const mockAgents: Record<string, { name: string; role: string; avatar: string }> = {
  "nancy-ai": {
    name: "Nancy AI",
    role: "Customer Service Specialist",
    avatar: "https://api.dicebear.com/7.x/avataaars/svg?seed=nancy",
  },
  "alex-sales": {
    name: "Alex AI",
    role: "Sales Representative",
    avatar: "https://api.dicebear.com/7.x/avataaars/svg?seed=alex",
  },
  "sophia-billing": {
    name: "Sophia AI",
    role: "Billing Officer",
    avatar: "https://api.dicebear.com/7.x/avataaars/svg?seed=sophia",
  },
};

const caseTitles = [
  "Refund request for overcharge",
  "Integration API token expired",
  "Cannot reset password",
  "VIP onboarding escalation",
  "SLA breach follow up",
  "Bug report: analytics dashboard",
];

const Sidebar: React.FC = () => {
  return (
    <aside className="hidden md:flex w-56 shrink-0 border-r border-border/70 bg-card/60 backdrop-blur-sm min-h-screen sticky top-0 sidebar-card-chrome">
      <div className="flex flex-col w-full p-3 gap-2">
        <div className="px-2 py-3 text-lg font-semibold">Cases</div>
        <nav className="mt-1 flex-1 space-y-1">
          {caseTitles.map((title) => (
            <Link
              key={title}
              to="#"
              className="w-full block text-left px-3 py-2 rounded-md text-sm text-foreground hover:bg-muted/60"
            >
              <span className="line-clamp-1">{title}</span>
            </Link>
          ))}
        </nav>
        <div className="mt-auto flex items-center gap-2 px-2 py-3">
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

const ChatPortal: React.FC = () => {
  const { agentId } = useParams<{ agentId: string }>();
  const navigate = useNavigate();
  const { toast } = useToast();

  // State
  const [messages, setMessages] = useState<Message[]>([]);
  const [isOnline, setIsOnline] = useState(true);
  const [isTyping, setIsTyping] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const abortRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Get agent data
  const agent = agentId ? mockAgents[agentId] : null;

  // Redirect if agent not found
  useEffect(() => {
    if (!agent && agentId) {
      toast({
        title: "Agent not found",
        description: "The requested agent does not exist.",
        variant: "destructive",
      });
      navigate("/");
    }
  }, [agent, agentId, navigate, toast]);

  // Initialize with welcome message
  useEffect(() => {
    if (agent) {
      const welcomeMessage: Message = {
        id: "welcome",
        content: `Hello! I'm ${agent.name}, your ${agent.role}. How can I assist you today?`,
        sender: "agent",
        timestamp: new Date(),
        agentName: agent.name,
        agentAvatar: agent.avatar,
      };

      const systemMessage: Message = {
        id: "system-start",
        content: "Session started",
        sender: "system",
        timestamp: new Date(),
      };

      setMessages([systemMessage, welcomeMessage]);
    }
  }, [agent]);

  // Handle sending message
  const handleSendMessage = async (content: string) => {
    if (!agent) return;

    // Add user message
    const userMessage: Message = {
      id: `user-${Date.now()}`,
      content,
      sender: "user",
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, userMessage]);

    // Simulate typing indicator
    setIsTyping(true);
    setIsLoading(true);

    // Simulate AI response delay
    abortRef.current = setTimeout(() => {
      setIsTyping(false);

      // Mock AI response
      const aiResponse: Message = {
        id: `agent-${Date.now()}`,
        content: `Thank you for your message! I understand you said: "${content}". How can I help you further?`,
        sender: "agent",
        timestamp: new Date(),
        agentName: agent.name,
        agentAvatar: agent.avatar,
      };

      setMessages((prev) => [...prev, aiResponse]);
      setIsLoading(false);
      abortRef.current = null;
    }, 1500 + Math.random() * 1000); // Random delay 1.5-2.5s
  };

  // Handle clear chat
  const handleClearChat = () => {
    if (!agent) return;

    setMessages([
      {
        id: "system-cleared",
        content: "Chat cleared",
        sender: "system",
        timestamp: new Date(),
      },
      {
        id: "welcome-new",
        content: `Hello again! I'm ${agent.name}. How can I help you?`,
        sender: "agent",
        timestamp: new Date(),
        agentName: agent.name,
        agentAvatar: agent.avatar,
      },
    ]);

    toast({
      title: "Chat cleared",
      description: "Conversation history has been cleared.",
    });
  };

  // Handle download transcript
  const handleDownloadTranscript = () => {
    const transcript = messages
      .filter((m) => m.sender !== "system")
      .map((m) => {
        const time = m.timestamp.toLocaleTimeString();
        const sender = m.sender === "agent" ? m.agentName : "You";
        return `[${time}] ${sender}: ${m.content}`;
      })
      .join("\n\n");

    const blob = new Blob([transcript], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `chat-transcript-${agentId}-${Date.now()}.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);

    toast({
      title: "Transcript downloaded",
      description: "Your conversation transcript has been saved.",
    });
  };

  // Handle report issue
  const handleReportIssue = () => {
    toast({
      title: "Report issue",
      description: "This feature will be available soon.",
    });
  };

  // Handle stop generation
  const handleStop = () => {
    if (abortRef.current) {
      clearTimeout(abortRef.current);
      abortRef.current = null;
    }
    setIsTyping(false);
    setIsLoading(false);
  };

  // Handle end session
  const handleEndSession = () => {
    toast({
      title: "Session ended",
      description: "Thank you for using our service.",
    });

    setTimeout(() => {
      navigate("/");
    }, 1500);
  };

  // Handle attach file
  const handleAttachFile = () => {
    toast({
      title: "File upload",
      description: "File upload feature coming soon.",
    });
  };

  if (!agent) {
    return null; // Will redirect in useEffect
  }

  return (
    <div className="min-h-screen bg-background flex">
      <Sidebar />
      <main className="flex-1 flex flex-col">
        {/* Header */}
        <ChatHeader
          agentName={agent.name}
          agentRole={agent.role}
          agentAvatar={agent.avatar}
          isOnline={isOnline}
          isTyping={isTyping}
          onClearChat={handleClearChat}
          onDownloadTranscript={handleDownloadTranscript}
          onReportIssue={handleReportIssue}
          onEndSession={handleEndSession}
        />

        {/* Messages area - with bottom padding for fixed input */}
        <div className="flex-1 overflow-hidden pb-[140px] pt-2 md:pt-4">
          <ChatMessages messages={messages} isLoading={isLoading} />
        </div>

        {/* Input area */}
        <ChatInput
          onSendMessage={handleSendMessage}
          onAttachFile={handleAttachFile}
          onStop={handleStop}
          isLoading={isLoading}
          disabled={!isOnline}
          className="md:left-[14rem] md:right-0"
        />
      </main>
    </div>
  );
};

export default ChatPortal;

