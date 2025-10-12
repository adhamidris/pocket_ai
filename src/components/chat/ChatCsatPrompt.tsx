import React, { useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

const RATING_OPTIONS = [1, 2, 3, 4, 5] as const;
const SCORE_MULTIPLIER = 20;

const ratingLabels: Record<number, string> = {
  1: "Very dissatisfied",
  2: "Dissatisfied",
  3: "Neutral",
  4: "Satisfied",
  5: "Very satisfied",
};

export interface ChatCsatPromptProps {
  onSubmit: (score: number, comment: string | null) => void;
  onSkip?: () => void;
  isSubmitting?: boolean;
}

const ChatCsatPrompt: React.FC<ChatCsatPromptProps> = ({ onSubmit, onSkip, isSubmitting = false }) => {
  const [rating, setRating] = useState<number | null>(null);
  const [comment, setComment] = useState("");

  const score = useMemo(() => (rating ? rating * SCORE_MULTIPLIER : null), [rating]);
  const selectedLabel = rating ? ratingLabels[rating] : null;

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!score || isSubmitting) return;
    onSubmit(score, comment.trim() ? comment.trim() : null);
  };

  return (
    <form
      onSubmit={handleSubmit}
      className="rounded-xl border border-border/60 bg-card/80 shadow-sm backdrop-blur-sm p-4 md:p-5 space-y-4"
    >
      <div>
        <h3 className="text-sm font-semibold text-foreground">How satisfied are you with your chat?</h3>
        <p className="text-xs text-muted-foreground mt-1">
          Your feedback helps us improve future conversations.
        </p>
      </div>

      <div className="flex items-center gap-2">
        {RATING_OPTIONS.map((value) => (
          <Button
            key={value}
            type="button"
            variant={rating === value ? "default" : "ghost"}
            size="sm"
            className={cn(
              "flex-1 h-10 rounded-lg border border-border/60 transition-all",
              rating === value && "bg-gradient-primary text-white shadow-md"
            )}
            onClick={() => setRating(value)}
            disabled={isSubmitting}
          >
            {value}
          </Button>
        ))}
      </div>

      {selectedLabel && (
        <div className="text-[11px] text-muted-foreground text-center">
          {selectedLabel}
        </div>
      )}

      <Textarea
        value={comment}
        onChange={(event) => setComment(event.target.value)}
        placeholder="Share additional feedback (optional)"
        rows={3}
        disabled={isSubmitting}
      />

      <div className="flex flex-wrap items-center justify-end gap-2">
        {onSkip && (
          <Button type="button" variant="ghost" size="sm" onClick={onSkip} disabled={isSubmitting}>
            Skip
          </Button>
        )}
        <Button type="submit" size="sm" disabled={!score || isSubmitting}>
          {isSubmitting ? "Sending..." : "Submit"}
        </Button>
      </div>
    </form>
  );
};

export default ChatCsatPrompt;
