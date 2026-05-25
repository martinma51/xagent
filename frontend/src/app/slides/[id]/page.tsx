"use client";

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, Download, Loader2, Sparkles, Wand2 } from "lucide-react";

import { apiRequest } from "@/lib/api-wrapper";
import { cn, getApiUrl } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import type {
  DeckSlotValues,
  SlideTemplateDetail,
} from "@/types/slide_template";

/**
 * Slide-template editor.
 *
 * Two-pane layout: a left form (tabbed by page) for filling slots and a right
 * preview pane showing the page thumbnail.  The "Download .pptx" button POSTs
 * the current slot values to /api/slide-templates/{id}/render and streams the
 * resulting binary into a browser download.
 */
export default function SlideTemplateEditorPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const apiBase = getApiUrl();

  const templateId = decodeURIComponent(params?.id ?? "");

  const [template, setTemplate] = useState<SlideTemplateDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [slotValues, setSlotValues] = useState<DeckSlotValues>({});
  const [activePage, setActivePage] = useState(0);
  const [rendering, setRendering] = useState(false);
  const [renderError, setRenderError] = useState<string | null>(null);

  // Live-preview state: filled HTML per page (cached) + scale for the iframe
  // so its fixed 1280×720 canvas fits the responsive preview container.
  const [previewHtml, setPreviewHtml] = useState<Record<number, string>>({});
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [scale, setScale] = useState(1);

  // Auto-fill state
  const [topic, setTopic] = useState("");
  const [autoFilling, setAutoFilling] = useState(false);
  const [autoFillError, setAutoFillError] = useState<string | null>(null);

  useEffect(() => {
    if (!templateId) return;
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await apiRequest(
          `${apiBase}/api/slide-templates/${encodeURIComponent(templateId)}`
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data: SlideTemplateDetail = await res.json();
        if (!cancelled) setTemplate(data);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [apiBase, templateId]);

  const updateSlot = useCallback(
    (pageIdx: number, slot: string, value: string) => {
      setSlotValues((prev) => ({
        ...prev,
        [pageIdx]: { ...(prev[pageIdx] ?? {}), [slot]: value },
      }));
    },
    []
  );

  // Debounced live preview: every change to the active page's slot values
  // POSTs to /preview/{idx} and drops the returned HTML into the iframe.
  useEffect(() => {
    if (!template) return;
    const idx = activePage;
    const values = slotValues[idx] ?? {};
    const controller = new AbortController();
    const handle = window.setTimeout(async () => {
      try {
        const res = await apiRequest(
          `${apiBase}/api/slide-templates/${encodeURIComponent(template.id)}/preview/${idx}`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ slot_values: values }),
            signal: controller.signal,
          }
        );
        if (!res.ok) return;
        const html = await res.text();
        setPreviewHtml((prev) => ({ ...prev, [idx]: html }));
      } catch {
        /* swallow — keep previous preview on error */
      }
    }, 250);
    return () => {
      clearTimeout(handle);
      controller.abort();
    };
  }, [apiBase, template, activePage, slotValues]);

  // Scale the 1280-wide iframe to whatever width the container ends up with.
  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const update = () => setScale(el.clientWidth / 1280);
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, [template]);

  const allRequiredSatisfied = useMemo(() => {
    if (!template) return false;
    for (const page of template.pages) {
      for (const [slotName, spec] of Object.entries(page.slots)) {
        if (spec.required && !slotValues[page.idx]?.[slotName]?.trim()) {
          return false;
        }
      }
    }
    return true;
  }, [template, slotValues]);

  const handleAutoFill = useCallback(async () => {
    if (!template || !topic.trim()) return;
    setAutoFilling(true);
    setAutoFillError(null);
    try {
      const res = await apiRequest(
        `${apiBase}/api/slide-templates/${encodeURIComponent(template.id)}/auto-fill`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ topic: topic.trim() }),
        }
      );
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
          const body = await res.json();
          detail =
            typeof body.detail === "string"
              ? body.detail
              : JSON.stringify(body.detail ?? body);
        } catch {
          /* not json */
        }
        throw new Error(detail);
      }
      const data = (await res.json()) as {
        slot_values: Record<string, Record<string, string>>;
      };
      // server returns string keys ("0", "1", ...); coerce to numeric DeckSlotValues
      const next: DeckSlotValues = {};
      for (const [k, v] of Object.entries(data.slot_values || {})) {
        next[Number(k)] = v ?? {};
      }
      setSlotValues(next);
    } catch (err) {
      setAutoFillError(err instanceof Error ? err.message : String(err));
    } finally {
      setAutoFilling(false);
    }
  }, [apiBase, template, topic]);

  const handleDownload = useCallback(async () => {
    if (!template) return;
    setRendering(true);
    setRenderError(null);
    try {
      const res = await apiRequest(
        `${apiBase}/api/slide-templates/${encodeURIComponent(template.id)}/render`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            slot_values: slotValues,
            filename: template.id,
          }),
        }
      );
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
          const body = await res.json();
          detail = JSON.stringify(body.detail ?? body);
        } catch {
          /* response wasn't json */
        }
        throw new Error(detail);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${template.id}.pptx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setRenderError(err instanceof Error ? err.message : String(err));
    } finally {
      setRendering(false);
    }
  }, [apiBase, slotValues, template]);

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        Loading template…
      </div>
    );
  }
  if (error || !template) {
    return (
      <div className="m-8 rounded-lg border border-destructive/30 bg-destructive/5 p-6 text-sm text-destructive">
        Could not load template: {error ?? "not found"}
      </div>
    );
  }

  const activePageInfo =
    template.pages.find((p) => p.idx === activePage) ?? template.pages[0];
  const activeThumb = template.thumbnail_urls[activePageInfo.idx];
  const currentHtml = previewHtml[activePageInfo.idx];

  return (
    <div className="flex h-full flex-col bg-background">
      {/* Top bar */}
      <div className="flex h-14 shrink-0 items-center justify-between border-b border-border/60 bg-background px-6">
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => router.push("/slides")}
            className="h-8"
          >
            <ArrowLeft className="mr-1 h-4 w-4" /> Back
          </Button>
          <span className="text-sm text-muted-foreground">/</span>
          <span className="font-semibold text-foreground">{template.name}</span>
          <span className="text-xs text-muted-foreground">
            {template.page_count} pages
          </span>
        </div>
        <div className="flex items-center gap-3">
          {renderError && (
            <span className="max-w-md truncate text-xs text-destructive">
              {renderError}
            </span>
          )}
          <Button
            onClick={handleDownload}
            disabled={!allRequiredSatisfied || rendering}
            size="sm"
          >
            {rendering ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Download className="mr-2 h-4 w-4" />
            )}
            Download .pptx
          </Button>
        </div>
      </div>

      {/* Two-pane body */}
      <div className="flex min-h-0 flex-1">
        {/* LEFT: form */}
        <div className="flex w-[420px] shrink-0 flex-col border-r border-border/60 bg-card/30">
          {/* auto-fill bar */}
          <div className="shrink-0 border-b border-border/60 bg-gradient-to-br from-primary/[0.06] to-transparent px-4 py-3">
            <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              <Wand2 className="h-3 w-3" /> Auto-fill with AI
            </div>
            <div className="flex gap-2">
              <input
                type="text"
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey && !autoFilling) {
                    e.preventDefault();
                    void handleAutoFill();
                  }
                }}
                placeholder="What is this deck about?"
                disabled={autoFilling}
                className={cn(
                  "flex-1 rounded-md border border-border bg-background px-3 py-1.5 text-sm",
                  "outline-none focus:border-primary focus:ring-2 focus:ring-primary/20",
                  "disabled:opacity-50"
                )}
              />
              <Button
                size="sm"
                onClick={() => void handleAutoFill()}
                disabled={!topic.trim() || autoFilling}
              >
                {autoFilling ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Wand2 className="h-3.5 w-3.5" />
                )}
              </Button>
            </div>
            {autoFillError && (
              <p className="mt-1.5 line-clamp-2 text-[11px] text-destructive">
                {autoFillError}
              </p>
            )}
          </div>

          {/* page tabs */}
          <div className="flex shrink-0 gap-1 overflow-x-auto border-b border-border/60 px-3 py-2">
            {template.pages.map((p) => (
              <button
                key={p.idx}
                onClick={() => setActivePage(p.idx)}
                className={cn(
                  "shrink-0 rounded-md px-3 py-1.5 text-xs font-medium transition",
                  activePage === p.idx
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-muted"
                )}
              >
                {p.idx + 1}. {p.layout}
              </button>
            ))}
          </div>
          {/* slot form for the active page */}
          <div className="flex-1 overflow-y-auto px-5 py-4">
            <div className="mb-3 flex items-center gap-2 text-xs uppercase tracking-wide text-muted-foreground">
              <Sparkles className="h-3 w-3" />
              Page {activePageInfo.idx + 1} — {activePageInfo.layout}
            </div>
            <div className="space-y-4">
              {Object.entries(activePageInfo.slots).map(
                ([slotName, spec]) => (
                  <SlotField
                    key={slotName}
                    name={slotName}
                    spec={spec}
                    value={slotValues[activePageInfo.idx]?.[slotName] ?? ""}
                    onChange={(v) =>
                      updateSlot(activePageInfo.idx, slotName, v)
                    }
                  />
                )
              )}
              {Object.keys(activePageInfo.slots).length === 0 && (
                <p className="text-sm text-muted-foreground">
                  This page has no editable fields.
                </p>
              )}
            </div>
          </div>
        </div>

        {/* RIGHT: preview */}
        <div className="flex flex-1 flex-col bg-muted/30">
          <div className="flex flex-1 items-center justify-center p-8">
            <div
              ref={containerRef}
              className="relative aspect-[16/9] w-full max-w-[1024px] overflow-hidden rounded-lg border border-border/60 bg-black shadow-sm"
            >
              {currentHtml ? (
                <iframe
                  title={`Page ${activePageInfo.idx + 1} preview`}
                  srcDoc={currentHtml}
                  style={{
                    width: 1280,
                    height: 720,
                    border: 0,
                    position: "absolute",
                    left: 0,
                    top: 0,
                    transform: `scale(${scale})`,
                    transformOrigin: "top left",
                    pointerEvents: "none",
                  }}
                />
              ) : activeThumb ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={`${apiBase}${activeThumb}`}
                  alt={`Page ${activePageInfo.idx + 1} preview`}
                  className="absolute inset-0 h-full w-full object-cover"
                />
              ) : (
                <div className="absolute inset-0 flex items-center justify-center text-sm text-muted-foreground">
                  No preview available.
                </div>
              )}
            </div>
          </div>
          <div className="flex shrink-0 items-center justify-center gap-2 border-t border-border/60 bg-background py-3">
            {template.pages.map((p) => (
              <button
                key={p.idx}
                onClick={() => setActivePage(p.idx)}
                className={cn(
                  "h-7 w-7 rounded-full text-xs font-medium transition",
                  activePage === p.idx
                    ? "bg-primary text-primary-foreground"
                    : "bg-muted text-muted-foreground hover:bg-muted/80"
                )}
                aria-label={`Go to page ${p.idx + 1}`}
              >
                {p.idx + 1}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function SlotField({
  name,
  spec,
  value,
  onChange,
}: {
  name: string;
  spec: { required: boolean; max_length?: number | null; hint?: string | null };
  value: string;
  onChange: (v: string) => void;
}) {
  const isLong = (spec.max_length ?? 0) > 80;
  return (
    <label className="block">
      <span className="mb-1 flex items-center gap-1 text-xs font-medium text-foreground">
        {name}
        {spec.required && <span className="text-destructive">*</span>}
        {spec.max_length && (
          <span className="ml-auto text-muted-foreground/70">
            {value.length}/{spec.max_length}
          </span>
        )}
      </span>
      {isLong ? (
        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          rows={3}
          maxLength={spec.max_length ?? undefined}
          className={cn(
            "w-full resize-none rounded-md border border-border bg-background px-3 py-2 text-sm",
            "outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
          )}
          placeholder={spec.hint ?? ""}
        />
      ) : (
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          maxLength={spec.max_length ?? undefined}
          className={cn(
            "w-full rounded-md border border-border bg-background px-3 py-2 text-sm",
            "outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
          )}
          placeholder={spec.hint ?? ""}
        />
      )}
      {spec.hint && (
        <span className="mt-1 block text-[11px] text-muted-foreground">
          {spec.hint}
        </span>
      )}
    </label>
  );
}
