"use client";

import {
  memo,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useParams, useRouter } from "next/navigation";
import {
  ArrowLeft,
  Check,
  ChevronRight,
  Download,
  GripVertical,
  Loader2,
  Plus,
  Search,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";

import { apiRequest } from "@/lib/api-wrapper";
import { cn, getApiUrl } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import type {
  DeckDetail,
  DeckPageEntry,
  SlideLayoutInfo,
} from "@/types/slide_deck";
import type { SlotSpec } from "@/types/slide_template";

/**
 * Deck editor (Phase A).
 *
 * Three columns:
 *   1. Left thumbnail list of every page in deck.pages, with add / delete /
 *      drag-to-reorder controls.  Pages can be drawn from any layout.
 *   2. Centre form for editing the active page's slot values, driven by the
 *      schema of the layout that page points at.
 *   3. Right live preview iframe.
 *
 * Edits are debounced into PUT /api/decks/{id} so the deck persists in the
 * background without a manual save button.  A small "Saved · just now"
 * indicator in the top bar confirms each commit.
 */
export default function DeckEditorPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const apiBase = getApiUrl();

  const deckId = Number(params?.id ?? "");
  const deckIdValid = Number.isFinite(deckId) && deckId > 0;

  const [deck, setDeck] = useState<DeckDetail | null>(null);
  const [layoutsById, setLayoutsById] = useState<Record<string, SlideLayoutInfo>>(
    {}
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [activePageIdx, setActivePageIdx] = useState(0);
  const [previewHtml, setPreviewHtml] = useState<Record<number, string>>({});
  const containerRef = useRef<HTMLDivElement | null>(null);
  const previewIframeRef = useRef<HTMLIFrameElement | null>(null);
  const [scale, setScale] = useState(1);

  type SaveState = "idle" | "saving" | "saved" | "error";
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [saveError, setSaveError] = useState<string | null>(null);

  const [showPicker, setShowPicker] = useState(false);
  const [pickerInsertAt, setPickerInsertAt] = useState(0);

  const draggingIdxRef = useRef<number | null>(null);
  const [dragOverIdx, setDragOverIdx] = useState<number | null>(null);

  const [rendering, setRendering] = useState(false);
  const [renderError, setRenderError] = useState<string | null>(null);

  // ----- manual-slot drawing (Phase C') ----------------------------------
  // Local-only state — never round-trips through the deck save path.
  const [drawingMode, setDrawingMode] = useState(false);
  const [dragRect, setDragRect] = useState<
    | { startX: number; startY: number; endX: number; endY: number }
    | null
  >(null);
  const [pendingSlot, setPendingSlot] = useState<
    | { x: number; y: number; width: number; height: number; name: string; text: string }
    | null
  >(null);
  const [slotSaving, setSlotSaving] = useState(false);
  const [slotError, setSlotError] = useState<string | null>(null);

  // ----- load deck + layouts --------------------------------------------
  const refetchLayouts = useCallback(async (): Promise<void> => {
    try {
      const res = await apiRequest(`${apiBase}/api/slide-layouts/`);
      if (!res.ok) return;
      const data: SlideLayoutInfo[] = await res.json();
      setLayoutsById(Object.fromEntries(data.map((l) => [l.id, l])));
    } catch {
      /* ignore — main load handler shows hard errors */
    }
  }, [apiBase]);

  useEffect(() => {
    if (!deckIdValid) {
      setLoading(false);
      setError("Invalid deck id");
      return;
    }
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const [deckRes, layoutsRes] = await Promise.all([
          apiRequest(`${apiBase}/api/decks/${deckId}`),
          apiRequest(`${apiBase}/api/slide-layouts/`),
        ]);
        if (!deckRes.ok) throw new Error(`deck HTTP ${deckRes.status}`);
        if (!layoutsRes.ok) throw new Error(`layouts HTTP ${layoutsRes.status}`);
        const deckData: DeckDetail = await deckRes.json();
        const layoutsData: SlideLayoutInfo[] = await layoutsRes.json();
        if (cancelled) return;
        setDeck(deckData);
        setLayoutsById(
          Object.fromEntries(layoutsData.map((l) => [l.id, l]))
        );
        setActivePageIdx(0);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [apiBase, deckId, deckIdValid]);

  // ----- debounced auto-save --------------------------------------------
  // We persist on every edit but coalesce bursts (typing) into one PUT.
  const saveTimer = useRef<number | null>(null);
  const pendingDeckRef = useRef<DeckDetail | null>(null);
  const scheduleSave = useCallback(
    (next: DeckDetail) => {
      pendingDeckRef.current = next;
      setSaveState("saving");
      if (saveTimer.current) window.clearTimeout(saveTimer.current);
      saveTimer.current = window.setTimeout(async () => {
        const snapshot = pendingDeckRef.current;
        if (!snapshot) return;
        try {
          const res = await apiRequest(
            `${apiBase}/api/decks/${snapshot.id}`,
            {
              method: "PUT",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                title: snapshot.title,
                topic: snapshot.topic,
                pages: snapshot.pages,
              }),
            }
          );
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
          setSaveState("saved");
          setSaveError(null);
        } catch (err) {
          setSaveState("error");
          setSaveError(err instanceof Error ? err.message : String(err));
        }
      }, 700);
    },
    [apiBase]
  );

  const mutateDeck = useCallback(
    (update: (prev: DeckDetail) => DeckDetail) => {
      setDeck((prev) => {
        if (!prev) return prev;
        const next = update(prev);
        scheduleSave(next);
        return next;
      });
    },
    [scheduleSave]
  );

  // ----- live preview ----------------------------------------------------
  // POST /api/decks/{id}/preview/{idx} with the *current* slot values (which
  // may not be persisted yet) so the iframe updates immediately on keystroke.
  useEffect(() => {
    if (!deck) return;
    if (activePageIdx >= deck.pages.length) return;
    const idx = activePageIdx;
    const slot_values = deck.pages[idx]?.slot_values ?? {};
    const controller = new AbortController();
    const handle = window.setTimeout(async () => {
      try {
        const res = await apiRequest(
          `${apiBase}/api/decks/${deck.id}/preview/${idx}`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ slot_values }),
            signal: controller.signal,
          }
        );
        if (!res.ok) return;
        const html = await res.text();
        setPreviewHtml((prev) => ({ ...prev, [idx]: html }));
      } catch {
        /* keep previous preview */
      }
    }, 200);
    return () => {
      clearTimeout(handle);
      controller.abort();
    };
  }, [apiBase, deck, activePageIdx]);

  // Write previewHtml into the iframe via document.open/write rather than
  // letting srcDoc remount the iframe on every change. srcDoc-replacement
  // tears the iframe down and re-paints, which shows up as a white flash
  // on every keystroke; doc.write swaps the document inside the same
  // iframe element so we still see a brief blank but no border/scale jump
  // and React doesn't reconcile.
  const currentHtmlForActive = previewHtml[activePageIdx] ?? "";
  useEffect(() => {
    const iframe = previewIframeRef.current;
    if (!iframe) return;
    const doc = iframe.contentDocument;
    if (!doc) return;
    doc.open();
    doc.write(currentHtmlForActive || "<!doctype html><html><body></body></html>");
    doc.close();
  }, [currentHtmlForActive, activePageIdx]);

  // ----- iframe scaling --------------------------------------------------
  // Watch the preview container's width and keep `scale` in sync. Keeping
  // [deck] in the dep array used to recreate the ResizeObserver on every
  // keystroke (deck changes whenever a slot value updates) — each
  // disconnect/reconnect could measure clientWidth slightly differently
  // and ripple a re-render through the whole editor. The observer only
  // needs to bind to the DOM node once.
  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const update = () => {
      const next = el.clientWidth / 1280;
      setScale((prev) => (Math.abs(prev - next) < 0.0005 ? prev : next));
    };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // ----- mutations --------------------------------------------------------
  const updateSlot = useCallback(
    (pageIdx: number, slotName: string, value: string) => {
      mutateDeck((prev) => {
        const pages = prev.pages.slice();
        const page = pages[pageIdx];
        if (!page) return prev;
        pages[pageIdx] = {
          ...page,
          slot_values: { ...page.slot_values, [slotName]: value },
        };
        return { ...prev, pages };
      });
    },
    [mutateDeck]
  );

  const insertLayout = useCallback(
    (layoutId: string, insertAt: number) => {
      mutateDeck((prev) => {
        const newEntry: DeckPageEntry = { layout_id: layoutId, slot_values: {} };
        const pages = [
          ...prev.pages.slice(0, insertAt),
          newEntry,
          ...prev.pages.slice(insertAt),
        ];
        return { ...prev, pages };
      });
      setActivePageIdx(insertAt);
      setShowPicker(false);
    },
    [mutateDeck]
  );

  const deletePage = useCallback(
    (pageIdx: number) => {
      mutateDeck((prev) => {
        if (prev.pages.length <= 1) return prev; // keep at least one
        const pages = prev.pages.filter((_, i) => i !== pageIdx);
        return { ...prev, pages };
      });
      setActivePageIdx((cur) => Math.max(0, cur > pageIdx ? cur - 1 : Math.min(cur, (deck?.pages.length ?? 1) - 2)));
      setPreviewHtml({}); // invalidate cached previews since indices shifted
    },
    [mutateDeck, deck]
  );

  const reorderPages = useCallback(
    (from: number, to: number) => {
      if (from === to) return;
      mutateDeck((prev) => {
        const pages = prev.pages.slice();
        const [moved] = pages.splice(from, 1);
        pages.splice(to, 0, moved);
        return { ...prev, pages };
      });
      setActivePageIdx((cur) => {
        if (cur === from) return to;
        if (from < cur && to >= cur) return cur - 1;
        if (from > cur && to <= cur) return cur + 1;
        return cur;
      });
      setPreviewHtml({});
    },
    [mutateDeck]
  );

  // ----- slot CRUD on user-uploaded templates (Phase C') ----------------
  const saveSlot = useCallback(
    async (
      templateId: string,
      pageIdx: number,
      rect: { x: number; y: number; width: number; height: number },
      name: string,
      defaultText: string
    ): Promise<boolean> => {
      setSlotSaving(true);
      setSlotError(null);
      try {
        const res = await apiRequest(
          `${apiBase}/api/user-slide-templates/${encodeURIComponent(
            templateId
          )}/pages/${pageIdx}/slots`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ...rect, name, default_text: defaultText }),
          }
        );
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
        await refetchLayouts();
        // Invalidate cached previews so the new overlay shows up.
        setPreviewHtml({});
        return true;
      } catch (err) {
        setSlotError(err instanceof Error ? err.message : String(err));
        return false;
      } finally {
        setSlotSaving(false);
      }
    },
    [apiBase, refetchLayouts]
  );

  const deleteSlot = useCallback(
    async (templateId: string, pageIdx: number, slotName: string): Promise<void> => {
      const ok = window.confirm(
        `Delete slot "${slotName}"? The text box is removed from this layout for every deck using it.`
      );
      if (!ok) return;
      setSlotSaving(true);
      setSlotError(null);
      try {
        const res = await apiRequest(
          `${apiBase}/api/user-slide-templates/${encodeURIComponent(
            templateId
          )}/pages/${pageIdx}/slots/${encodeURIComponent(slotName)}`,
          { method: "DELETE" }
        );
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
        await refetchLayouts();
        setPreviewHtml({});
        // Clear any value the user typed for this slot so it doesn't linger.
        mutateDeck((prev) => {
          const pages = prev.pages.slice();
          const page = pages[activePageIdx];
          if (page) {
            const { [slotName]: _drop, ...rest } = page.slot_values;
            pages[activePageIdx] = { ...page, slot_values: rest };
          }
          return { ...prev, pages };
        });
      } catch (err) {
        setSlotError(err instanceof Error ? err.message : String(err));
      } finally {
        setSlotSaving(false);
      }
    },
    [apiBase, activePageIdx, mutateDeck, refetchLayouts]
  );

  // ----- download pptx ---------------------------------------------------
  const handleDownload = useCallback(async () => {
    if (!deck) return;
    setRendering(true);
    setRenderError(null);
    try {
      const res = await apiRequest(`${apiBase}/api/decks/${deck.id}/render`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: deck.title || `deck_${deck.id}` }),
      });
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
          const body = await res.json();
          detail = JSON.stringify(body.detail ?? body);
        } catch {
          /* not json */
        }
        throw new Error(detail);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${deck.title || `deck_${deck.id}`}.pptx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setRenderError(err instanceof Error ? err.message : String(err));
    } finally {
      setRendering(false);
    }
  }, [apiBase, deck]);

  // ----- render ----------------------------------------------------------
  const activeLayout = useMemo<SlideLayoutInfo | undefined>(() => {
    if (!deck) return undefined;
    const entry = deck.pages[activePageIdx];
    if (!entry) return undefined;
    return layoutsById[entry.layout_id];
  }, [deck, activePageIdx, layoutsById]);

  if (!deckIdValid) {
    return (
      <ErrorState message="Invalid deck id" onBack={() => router.push("/slides")} />
    );
  }
  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        Loading deck…
      </div>
    );
  }
  if (error || !deck) {
    return (
      <ErrorState
        message={error ?? "Deck not found"}
        onBack={() => router.push("/slides")}
      />
    );
  }

  return (
    <div className="flex h-full flex-col bg-background">
      {/* Top bar */}
      <div className="flex h-14 shrink-0 items-center justify-between border-b border-border/60 bg-background px-6">
        <div className="flex min-w-0 items-center gap-3">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => router.push("/slides")}
            className="h-8"
          >
            <ArrowLeft className="mr-1 h-4 w-4" /> Back
          </Button>
          <span className="text-sm text-muted-foreground">/</span>
          <input
            value={deck.title}
            onChange={(e) =>
              mutateDeck((prev) => ({ ...prev, title: e.target.value }))
            }
            placeholder="Untitled deck"
            className="min-w-0 max-w-[400px] bg-transparent text-sm font-semibold text-foreground outline-none placeholder:text-muted-foreground/60"
          />
          <span className="shrink-0 text-xs text-muted-foreground">
            {deck.pages.length} pages
          </span>
          {deck.template_id && (
            <span className="shrink-0 text-xs text-muted-foreground/70">
              · based on {deck.template_id}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          <SaveIndicator state={saveState} error={saveError} />
          {renderError && (
            <span className="max-w-md truncate text-xs text-destructive">
              {renderError}
            </span>
          )}
          <Button
            onClick={handleDownload}
            disabled={rendering || deck.pages.length === 0}
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

      {/* Three-pane body */}
      <div className="flex min-h-0 flex-1">
        {/* LEFT: page list */}
        <div className="flex w-[220px] shrink-0 flex-col border-r border-border/60 bg-card/30">
          <div className="flex shrink-0 items-center justify-between px-3 py-3">
            <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Pages
            </span>
            <button
              onClick={() => {
                setPickerInsertAt(deck.pages.length);
                setShowPicker(true);
              }}
              className="flex h-7 items-center gap-1 rounded-md bg-primary px-2 text-xs font-medium text-primary-foreground hover:bg-primary/90"
            >
              <Plus className="h-3 w-3" />
              Add
            </button>
          </div>
          <div className="flex-1 overflow-y-auto px-2 pb-3">
            {deck.pages.map((entry, idx) => {
              const layout = layoutsById[entry.layout_id];
              const isActive = idx === activePageIdx;
              const isDragOver = dragOverIdx === idx;
              return (
                <PageThumb
                  key={`${entry.layout_id}-${idx}`}
                  idx={idx}
                  entry={entry}
                  layout={layout}
                  apiBase={apiBase}
                  active={isActive}
                  dragOver={isDragOver}
                  onSelect={() => setActivePageIdx(idx)}
                  onDelete={
                    deck.pages.length > 1 ? () => deletePage(idx) : undefined
                  }
                  onInsertAfter={() => {
                    setPickerInsertAt(idx + 1);
                    setShowPicker(true);
                  }}
                  onDragStart={() => {
                    draggingIdxRef.current = idx;
                  }}
                  onDragOver={() => setDragOverIdx(idx)}
                  onDragEnd={() => {
                    setDragOverIdx(null);
                    draggingIdxRef.current = null;
                  }}
                  onDrop={() => {
                    const from = draggingIdxRef.current;
                    if (from !== null) reorderPages(from, idx);
                    setDragOverIdx(null);
                    draggingIdxRef.current = null;
                  }}
                />
              );
            })}
          </div>
        </div>

        {/* CENTRE: slot editor */}
        <div className="flex w-[360px] shrink-0 flex-col border-r border-border/60 bg-card/30">
          <div className="shrink-0 border-b border-border/60 px-5 py-3">
            <div className="flex items-center gap-2 text-xs uppercase tracking-wide text-muted-foreground">
              <Sparkles className="h-3 w-3" />
              Page {activePageIdx + 1}
              {activeLayout && (
                <span className="ml-1 text-muted-foreground/60">
                  · {activeLayout.template_name} / {activeLayout.layout}
                </span>
              )}
            </div>
            {activeLayout?.is_user_uploaded && (
              <div className="mt-2 flex items-center gap-2">
                <Button
                  size="sm"
                  variant={drawingMode ? "default" : "outline"}
                  className="h-7 px-2 text-xs"
                  onClick={() => {
                    setDrawingMode((d) => !d);
                    setDragRect(null);
                    setPendingSlot(null);
                    setSlotError(null);
                  }}
                >
                  {drawingMode ? (
                    <>
                      <X className="mr-1 h-3 w-3" /> Exit draw mode
                    </>
                  ) : (
                    <>
                      <Plus className="mr-1 h-3 w-3" /> Add text box
                    </>
                  )}
                </Button>
                <span className="text-[11px] text-muted-foreground/70">
                  {drawingMode
                    ? "Drag on the preview to draw a slot."
                    : "Draw new editable text boxes on the slide."}
                </span>
              </div>
            )}
            {slotError && (
              <div className="mt-2 rounded-md border border-destructive/30 bg-destructive/5 px-2 py-1 text-[11px] text-destructive">
                {slotError}
              </div>
            )}
          </div>
          <div className="flex-1 overflow-y-auto px-5 py-4">
            {!activeLayout ? (
              <p className="text-sm text-destructive">
                Layout “{deck.pages[activePageIdx]?.layout_id}” no longer
                exists. Delete this page and add a new one.
              </p>
            ) : Object.keys(activeLayout.slots).length === 0 ? (
              <p className="text-sm text-muted-foreground">
                {activeLayout.is_user_uploaded
                  ? "No editable fields yet — click \"Add text box\" above and drag on the preview."
                  : "This layout has no editable fields."}
              </p>
            ) : (
              <div className="space-y-4">
                {Object.entries(activeLayout.slots).map(
                  ([slotName, spec]) => (
                    <SlotField
                      key={slotName}
                      name={slotName}
                      spec={spec}
                      value={
                        deck.pages[activePageIdx]?.slot_values[slotName] ?? ""
                      }
                      onChange={(v) =>
                        updateSlot(activePageIdx, slotName, v)
                      }
                      onDelete={
                        spec.origin === "manual" && activeLayout.is_user_uploaded
                          ? () =>
                              void deleteSlot(
                                activeLayout.template_id,
                                activeLayout.page_idx,
                                slotName
                              )
                          : undefined
                      }
                      deleting={slotSaving}
                    />
                  )
                )}
              </div>
            )}
          </div>
        </div>

        {/* RIGHT: preview */}
        <div className="flex flex-1 flex-col bg-muted/30">
          <div className="flex flex-1 items-center justify-center p-8">
            <div
              ref={containerRef}
              className={cn(
                "relative aspect-[16/9] w-full max-w-[1024px] overflow-hidden rounded-lg border border-border/60 bg-black shadow-sm",
                drawingMode && "cursor-crosshair border-primary"
              )}
              onMouseDown={(e) => {
                if (!drawingMode || pendingSlot) return;
                const rect = e.currentTarget.getBoundingClientRect();
                const x = e.clientX - rect.left;
                const y = e.clientY - rect.top;
                setDragRect({ startX: x, startY: y, endX: x, endY: y });
              }}
              onMouseMove={(e) => {
                if (!dragRect) return;
                const rect = e.currentTarget.getBoundingClientRect();
                setDragRect({
                  ...dragRect,
                  endX: Math.max(0, Math.min(rect.width, e.clientX - rect.left)),
                  endY: Math.max(0, Math.min(rect.height, e.clientY - rect.top)),
                });
              }}
              onMouseUp={(e) => {
                if (!dragRect || !activeLayout) return;
                const rect = e.currentTarget.getBoundingClientRect();
                const endX = Math.max(0, Math.min(rect.width, e.clientX - rect.left));
                const endY = Math.max(0, Math.min(rect.height, e.clientY - rect.top));
                const screenX = Math.min(dragRect.startX, endX);
                const screenY = Math.min(dragRect.startY, endY);
                const screenW = Math.abs(endX - dragRect.startX);
                const screenH = Math.abs(endY - dragRect.startY);
                setDragRect(null);
                if (screenW < 10 || screenH < 10) return; // ignore micro-drags
                // Convert preview-space → 1280×720 canvas coords using `scale`.
                const s = scale || 1;
                setPendingSlot({
                  x: Math.round(screenX / s),
                  y: Math.round(screenY / s),
                  width: Math.round(screenW / s),
                  height: Math.round(screenH / s),
                  name: `slot_${Object.keys(activeLayout.slots).length + 1}`,
                  text: "Text",
                });
              }}
            >
              {/* Static template thumbnail sits underneath the iframe as a
                  fallback layer. While the iframe is between doc.write calls
                  (the brief blank moment on each preview refresh), this image
                  shows through so the user sees the slide instead of a white
                  flash. */}
              {activeLayout && (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={`${apiBase}${activeLayout.thumbnail_url}`}
                  alt={`Page ${activePageIdx + 1} preview`}
                  className="absolute inset-0 h-full w-full object-cover"
                  draggable={false}
                />
              )}
              <iframe
                ref={previewIframeRef}
                title={`Page ${activePageIdx + 1} preview`}
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
                  background: "transparent",
                }}
              />
              {!activeLayout && (
                <div className="absolute inset-0 flex items-center justify-center text-sm text-muted-foreground">
                  No preview available.
                </div>
              )}

              {/* Live rectangle while dragging */}
              {dragRect && (
                <div
                  className="pointer-events-none absolute border-2 border-primary bg-primary/15"
                  style={{
                    left: Math.min(dragRect.startX, dragRect.endX),
                    top: Math.min(dragRect.startY, dragRect.endY),
                    width: Math.abs(dragRect.endX - dragRect.startX),
                    height: Math.abs(dragRect.endY - dragRect.startY),
                  }}
                />
              )}

              {/* Inline form on top of finalised rectangle */}
              {pendingSlot && activeLayout && (
                <PendingSlotForm
                  slot={pendingSlot}
                  scale={scale}
                  saving={slotSaving}
                  onChange={(patch) => setPendingSlot({ ...pendingSlot, ...patch })}
                  onCancel={() => {
                    setPendingSlot(null);
                    setSlotError(null);
                  }}
                  onSave={async () => {
                    const ok = await saveSlot(
                      activeLayout.template_id,
                      activeLayout.page_idx,
                      {
                        x: pendingSlot.x,
                        y: pendingSlot.y,
                        width: pendingSlot.width,
                        height: pendingSlot.height,
                      },
                      pendingSlot.name,
                      pendingSlot.text
                    );
                    if (ok) {
                      setPendingSlot(null);
                      setDrawingMode(false);
                    }
                  }}
                />
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Layout picker modal */}
      {showPicker && (
        <LayoutPicker
          layouts={Object.values(layoutsById)}
          apiBase={apiBase}
          onPick={(layoutId) => insertLayout(layoutId, pickerInsertAt)}
          onClose={() => setShowPicker(false)}
        />
      )}
    </div>
  );
}

// ===== Sub-components =====

function ErrorState({
  message,
  onBack,
}: {
  message: string;
  onBack: () => void;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 text-center">
      <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-6 text-sm text-destructive">
        {message}
      </div>
      <Button variant="ghost" size="sm" onClick={onBack}>
        <ArrowLeft className="mr-1 h-4 w-4" /> Back to gallery
      </Button>
    </div>
  );
}

function SaveIndicator({
  state,
  error,
}: {
  state: "idle" | "saving" | "saved" | "error";
  error: string | null;
}) {
  if (state === "saving") {
    return (
      <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <Loader2 className="h-3 w-3 animate-spin" />
        Saving…
      </span>
    );
  }
  if (state === "saved") {
    return (
      <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <Check className="h-3 w-3 text-emerald-500" />
        Saved
      </span>
    );
  }
  if (state === "error") {
    return (
      <span
        className="max-w-[280px] truncate text-xs text-destructive"
        title={error ?? undefined}
      >
        Save failed: {error}
      </span>
    );
  }
  return null;
}

const PageThumb = memo(function PageThumb({
  idx,
  entry,
  layout,
  apiBase,
  active,
  dragOver,
  onSelect,
  onDelete,
  onInsertAfter,
  onDragStart,
  onDragOver,
  onDragEnd,
  onDrop,
}: {
  idx: number;
  entry: DeckPageEntry;
  layout: SlideLayoutInfo | undefined;
  apiBase: string;
  active: boolean;
  dragOver: boolean;
  onSelect: () => void;
  onDelete?: () => void;
  onInsertAfter: () => void;
  onDragStart: () => void;
  onDragOver: () => void;
  onDragEnd: () => void;
  onDrop: () => void;
}) {
  return (
    <div className="group relative">
      <div
        draggable
        onDragStart={(e) => {
          e.dataTransfer.effectAllowed = "move";
          onDragStart();
        }}
        onDragOver={(e) => {
          e.preventDefault();
          e.dataTransfer.dropEffect = "move";
          onDragOver();
        }}
        onDragEnd={onDragEnd}
        onDrop={(e) => {
          e.preventDefault();
          onDrop();
        }}
        onClick={onSelect}
        className={cn(
          "group/inner mb-2 flex cursor-pointer items-stretch gap-2 rounded-md border bg-card p-1.5 transition",
          active
            ? "border-primary ring-2 ring-primary/30"
            : "border-border/60 hover:border-primary/40",
          dragOver && "border-primary/60"
        )}
      >
        <div className="flex flex-col items-center justify-center px-0.5 text-muted-foreground/40 group-hover/inner:text-muted-foreground/70">
          <GripVertical className="h-3 w-3" />
        </div>
        <div className="flex-1">
          <div className="relative aspect-[16/9] w-full overflow-hidden rounded bg-muted">
            {layout ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={`${apiBase}${layout.thumbnail_url}`}
                alt={`Page ${idx + 1}`}
                className="absolute inset-0 h-full w-full object-cover"
                draggable={false}
              />
            ) : (
              <div className="absolute inset-0 flex items-center justify-center text-[10px] text-destructive">
                missing layout
              </div>
            )}
            <div className="absolute left-1 top-1 rounded bg-background/85 px-1.5 py-0.5 text-[10px] font-semibold text-foreground">
              {idx + 1}
            </div>
            {onDelete && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onDelete();
                }}
                className="absolute right-1 top-1 hidden h-5 w-5 items-center justify-center rounded bg-background/85 text-destructive hover:bg-destructive hover:text-destructive-foreground group-hover/inner:flex"
                aria-label="Delete page"
              >
                <Trash2 className="h-3 w-3" />
              </button>
            )}
          </div>
          <div
            className="mt-1 truncate text-[11px] text-muted-foreground"
            title={layout ? `${layout.template_name} / ${layout.layout}` : entry.layout_id}
          >
            {layout ? layout.layout || layout.template_name : entry.layout_id}
          </div>
        </div>
      </div>
      {/* Inline "+ insert below" trigger */}
      <button
        onClick={onInsertAfter}
        className="mb-2 flex h-5 w-full items-center justify-center rounded text-[10px] text-muted-foreground/60 opacity-0 transition hover:bg-muted hover:text-primary group-hover:opacity-100"
        aria-label="Insert page below"
      >
        <Plus className="mr-1 h-3 w-3" />
        Insert here
      </button>
    </div>
  );
});

function SlotField({
  name,
  spec,
  value,
  onChange,
  onDelete,
  deleting,
}: {
  name: string;
  spec: SlotSpec;
  value: string;
  onChange: (v: string) => void;
  onDelete?: () => void;
  deleting?: boolean;
}) {
  const isLong = (spec.max_length ?? 0) > 80;
  return (
    <label className="block">
      <span className="mb-1 flex items-center gap-1 text-xs font-medium text-foreground">
        {name}
        {spec.required && <span className="text-destructive">*</span>}
        {spec.origin === "manual" && (
          <span className="rounded bg-primary/10 px-1 py-0.5 text-[10px] uppercase tracking-wide text-primary">
            manual
          </span>
        )}
        {spec.max_length && (
          <span className="ml-auto text-muted-foreground/70">
            {value.length}/{spec.max_length}
          </span>
        )}
        {onDelete && (
          <button
            type="button"
            onClick={onDelete}
            disabled={deleting}
            className="ml-1 flex h-5 w-5 items-center justify-center rounded text-muted-foreground hover:bg-destructive/15 hover:text-destructive disabled:opacity-50"
            title={`Delete slot "${name}"`}
            aria-label={`Delete slot ${name}`}
          >
            <Trash2 className="h-3 w-3" />
          </button>
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

function PendingSlotForm({
  slot,
  scale,
  saving,
  onChange,
  onCancel,
  onSave,
}: {
  slot: { x: number; y: number; width: number; height: number; name: string; text: string };
  scale: number;
  saving: boolean;
  onChange: (patch: Partial<typeof slot>) => void;
  onCancel: () => void;
  onSave: () => void;
}) {
  // Position the form right under the drawn rectangle when there's room,
  // otherwise float it above so it doesn't get clipped by the container's
  // overflow-hidden. Math is all in preview-space (post-scale).
  const previewX = slot.x * scale;
  const previewY = slot.y * scale;
  const previewW = slot.width * scale;
  const previewH = slot.height * scale;
  const FORM_H = 220; // rough — input + textarea + buttons
  const containerH = 720 * scale;
  const flipAbove = previewY + previewH + FORM_H > containerH;
  const formTop = flipAbove
    ? Math.max(8, previewY - FORM_H - 6)
    : previewY + previewH + 6;
  return (
    <>
      {/* The frozen rectangle outline */}
      <div
        className="pointer-events-none absolute border-2 border-primary"
        style={{ left: previewX, top: previewY, width: previewW, height: previewH }}
      />
      <div
        className="absolute z-10 w-[260px] rounded-md border border-border bg-background p-3 shadow-lg"
        style={{ left: Math.max(8, previewX), top: formTop }}
        onClick={(e) => e.stopPropagation()}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="mb-2 text-[11px] text-muted-foreground">
          {slot.width}×{slot.height} px · at ({slot.x}, {slot.y})
        </div>
        <label className="mb-2 block">
          <span className="mb-1 block text-[11px] font-medium text-foreground">
            Slot name
          </span>
          <input
            type="text"
            value={slot.name}
            onChange={(e) => onChange({ name: e.target.value })}
            placeholder="title"
            className="w-full rounded border border-border bg-background px-2 py-1 text-xs outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
            autoFocus
          />
        </label>
        <label className="mb-2 block">
          <span className="mb-1 block text-[11px] font-medium text-foreground">
            Default text
          </span>
          <textarea
            value={slot.text}
            onChange={(e) => onChange({ text: e.target.value })}
            rows={2}
            className="w-full resize-none rounded border border-border bg-background px-2 py-1 text-xs outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
          />
        </label>
        <div className="flex justify-end gap-2">
          <Button
            size="sm"
            variant="ghost"
            className="h-7 px-2 text-xs"
            onClick={onCancel}
            disabled={saving}
          >
            Cancel
          </Button>
          <Button
            size="sm"
            className="h-7 px-2 text-xs"
            onClick={onSave}
            disabled={saving || !slot.name.trim()}
          >
            {saving ? (
              <Loader2 className="mr-1 h-3 w-3 animate-spin" />
            ) : (
              <Check className="mr-1 h-3 w-3" />
            )}
            Save
          </Button>
        </div>
      </div>
    </>
  );
}

function LayoutPicker({
  layouts,
  apiBase,
  onPick,
  onClose,
}: {
  layouts: SlideLayoutInfo[];
  apiBase: string;
  onPick: (layoutId: string) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const byTemplate = new Map<string, SlideLayoutInfo[]>();
    for (const l of layouts) {
      const matches =
        !q ||
        l.template_name.toLowerCase().includes(q) ||
        l.layout.toLowerCase().includes(q) ||
        l.template_id.toLowerCase().includes(q);
      if (!matches) continue;
      const arr = byTemplate.get(l.template_id) ?? [];
      arr.push(l);
      byTemplate.set(l.template_id, arr);
    }
    return Array.from(byTemplate.entries()).map(([tplId, items]) => ({
      tplId,
      tplName: items[0].template_name,
      category: items[0].template_category,
      items: items.sort((a, b) => a.page_idx - b.page_idx),
    }));
  }, [layouts, query]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-6"
      onClick={onClose}
    >
      <div
        className="relative flex h-[85vh] w-full max-w-5xl flex-col overflow-hidden rounded-xl border border-border bg-background shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex shrink-0 items-center justify-between border-b border-border/60 px-6 py-4">
          <div>
            <h2 className="text-lg font-semibold">Add a page</h2>
            <p className="text-xs text-muted-foreground">
              Pick any layout from any template — the deck is yours to compose.
            </p>
          </div>
          <button
            onClick={onClose}
            className="rounded-md p-1 text-muted-foreground hover:bg-muted"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="shrink-0 border-b border-border/60 px-6 py-3">
          <div className="relative w-full">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search by template name or layout…"
              className={cn(
                "h-10 w-full rounded-md border border-border bg-card pl-9 pr-3 text-sm",
                "outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
              )}
              autoFocus
            />
          </div>
        </div>
        <div className="flex-1 overflow-y-auto px-6 py-4">
          {groups.length === 0 ? (
            <p className="py-12 text-center text-sm text-muted-foreground">
              No layouts match.
            </p>
          ) : (
            <div className="space-y-6">
              {groups.map((g) => (
                <div key={g.tplId}>
                  <div className="mb-2 flex items-center gap-2 text-xs uppercase tracking-wide text-muted-foreground">
                    <span className="font-semibold text-foreground">
                      {g.tplName}
                    </span>
                    {g.category && (
                      <span className="rounded-full bg-muted px-2 py-0.5 text-[10px]">
                        {g.category}
                      </span>
                    )}
                    <ChevronRight className="h-3 w-3" />
                  </div>
                  <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
                    {g.items.map((l) => (
                      <button
                        key={l.id}
                        onClick={() => onPick(l.id)}
                        className={cn(
                          "group/layout flex flex-col overflow-hidden rounded-lg border border-border bg-card text-left transition",
                          "hover:border-primary/40 hover:shadow-md"
                        )}
                      >
                        <div className="relative aspect-[16/9] w-full overflow-hidden bg-muted">
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img
                            src={`${apiBase}${l.thumbnail_url}`}
                            alt={`${l.template_name} ${l.layout}`}
                            className="absolute inset-0 h-full w-full object-cover"
                          />
                        </div>
                        <div className="px-2 py-1.5">
                          <div className="truncate text-xs font-medium text-foreground">
                            {l.layout || `page ${l.page_idx + 1}`}
                          </div>
                          <div className="truncate text-[10px] text-muted-foreground">
                            {l.template_name}
                          </div>
                        </div>
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
