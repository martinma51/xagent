"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  AlertCircle,
  ArrowLeft,
  ChevronRight,
  Loader2,
  RefreshCw,
  Sparkles,
} from "lucide-react";

import { apiRequest } from "@/lib/api-wrapper";
import { getApiUrl } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import type { DeckDetail } from "@/types/slide_deck";

/**
 * /generate/[id] — intermediate task page (Phase D5).
 *
 * The xAgent Widgets v0.4 spec (page 9) shows users being routed to the
 * tasks page after Generate, where a "Thinking…" state is visible while
 * the LLM works, then they jump to the result. This route is the
 * slides-specific version of that: it polls ``GET /api/decks/{id}`` every
 * 1.5 s and watches ``generation_status``:
 *
 *   - "pending"  → keep polling, show a spinner + topic
 *   - "done"     → router.replace(`/slides/${id}`) so back button skips
 *                  this intermediate page
 *   - "error"    → show the message + Retry / Back actions
 *   - null/unset → treat as "done" (legacy / non-AI-created deck)
 *
 * The deck row is already persisted when this page loads, so a refresh
 * never loses progress and the LLM fill keeps running in the background
 * even if the user closes the tab.
 */
export default function GenerateDeckPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const apiBase = getApiUrl();
  const deckIdParam = Number(params?.id ?? "");
  const deckIdValid = Number.isFinite(deckIdParam) && deckIdParam > 0;

  const [deck, setDeck] = useState<DeckDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [elapsedSec, setElapsedSec] = useState(0);
  const startRef = useRef<number>(Date.now());
  const pollingRef = useRef<boolean>(true);

  const fetchDeck = useCallback(async (): Promise<void> => {
    try {
      const res = await apiRequest(`${apiBase}/api/decks/${deckIdParam}`);
      if (!res.ok) {
        if (res.status === 404) {
          setError(
            "This deck was deleted while we were generating it. Head back and try again."
          );
          pollingRef.current = false;
          return;
        }
        throw new Error(`HTTP ${res.status}`);
      }
      const data: DeckDetail = await res.json();
      setDeck(data);
      const status = data.generation_status ?? "done";
      if (status === "done") {
        pollingRef.current = false;
        router.replace(`/slides/${deckIdParam}`);
      } else if (status === "error") {
        pollingRef.current = false;
        setError(data.generation_error || "Generation failed.");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [apiBase, deckIdParam, router]);

  // Kick off polling.
  useEffect(() => {
    if (!deckIdValid) {
      setError("Invalid deck id");
      return;
    }
    pollingRef.current = true;
    void fetchDeck();
    const interval = window.setInterval(() => {
      if (!pollingRef.current) return;
      void fetchDeck();
    }, 1500);
    return () => {
      pollingRef.current = false;
      window.clearInterval(interval);
    };
  }, [deckIdValid, fetchDeck]);

  // Elapsed-time counter (purely cosmetic — gives the spinner context).
  useEffect(() => {
    const t = window.setInterval(() => {
      setElapsedSec(Math.floor((Date.now() - startRef.current) / 1000));
    }, 500);
    return () => window.clearInterval(t);
  }, []);

  const handleRetry = useCallback(async () => {
    if (!deck) return;
    setError(null);
    // POST a fresh generate against the same template + topic; the old
    // errored deck is left in the user's library for them to delete or
    // edit manually.
    try {
      const res = await apiRequest(`${apiBase}/api/decks/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          template_id: deck.template_id,
          topic: deck.topic,
        }),
      });
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
          const body = await res.json();
          detail = typeof body.detail === "string" ? body.detail : detail;
        } catch {
          /* not json */
        }
        throw new Error(detail);
      }
      const fresh: DeckDetail = await res.json();
      router.replace(`/generate/${fresh.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [apiBase, deck, router]);

  const handleBack = useCallback(() => {
    router.push("/slides");
  }, [router]);

  const status = deck?.generation_status ?? "done";

  return (
    <div className="flex h-full flex-col bg-background">
      {/* Top bar */}
      <div className="flex items-center justify-between border-b border-border/60 px-6 py-3">
        <button
          onClick={handleBack}
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          <ArrowLeft className="h-4 w-4" /> Back to AI Slides
        </button>
        <div className="text-xs text-muted-foreground">
          Run #{deckIdParam}
        </div>
      </div>

      {/* Center — Thinking / Error state */}
      <div className="flex flex-1 flex-col items-center justify-center px-6">
        {error ? (
          <div className="w-full max-w-xl rounded-2xl border border-destructive/30 bg-destructive/5 p-8 text-center">
            <AlertCircle className="mx-auto mb-3 h-8 w-8 text-destructive" />
            <h2 className="mb-2 text-lg font-semibold text-foreground">
              Generation didn’t complete
            </h2>
            <p className="mb-6 text-sm text-muted-foreground whitespace-pre-line">
              {error}
            </p>
            <div className="flex justify-center gap-2">
              <Button variant="outline" size="sm" onClick={handleBack}>
                Back
              </Button>
              {deck && status === "error" && (
                <Button size="sm" onClick={() => void handleRetry()}>
                  <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
                  Retry
                </Button>
              )}
            </div>
          </div>
        ) : (
          <div className="w-full max-w-xl text-center">
            <div className="mb-6 inline-flex h-16 w-16 items-center justify-center rounded-full bg-primary/10">
              <Loader2 className="h-7 w-7 animate-spin text-primary" />
            </div>
            <h2 className="mb-2 text-xl font-semibold text-foreground">
              {deck?.title || "Drafting your deck"}
            </h2>
            <p className="mb-4 text-sm text-muted-foreground">
              <Sparkles className="mr-1 inline h-3.5 w-3.5 text-primary" />
              Thinking… filling every slot with AI. This usually takes
              10–30 seconds.
            </p>
            {deck?.topic && (
              <p className="mb-6 max-w-md mx-auto text-sm italic text-muted-foreground">
                “{deck.topic}”
              </p>
            )}
            <div className="text-xs text-muted-foreground/70">
              {elapsedSec}s elapsed
            </div>

            <div className="mt-10 flex items-center justify-center gap-2 text-xs text-muted-foreground">
              <ChevronRight className="h-3 w-3" />
              You’ll be redirected to the editor as soon as the deck is
              ready.
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
