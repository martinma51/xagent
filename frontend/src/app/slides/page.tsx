"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Search,
  Layers,
  ChevronRight,
  ChevronLeft,
  Loader2,
  Upload,
  X,
  Trash2,
  Paperclip,
  Sparkles,
} from "lucide-react";
import Link from "next/link";

import { apiRequest } from "@/lib/api-wrapper";
import { cn, getApiUrl } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { SlideTemplateInfo } from "@/types/slide_template";
import type { DeckDetail } from "@/types/slide_deck";

/**
 * Slide-template gallery.
 *
 * Lists every deck template the backend exposes at GET /api/slide-templates/,
 * with category pills + free-text search.  Picking a card navigates to the
 * editor page where the user fills slots and downloads a .pptx.
 */
export default function SlidesGalleryPage() {
  const router = useRouter();
  const apiBase = getApiUrl();

  const [templates, setTemplates] = useState<SlideTemplateInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedCategory, setSelectedCategory] = useState("All");
  const [creatingFromTemplate, setCreatingFromTemplate] = useState<string | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [promptText, setPromptText] = useState("");
  const [selectedTemplateId, setSelectedTemplateId] = useState<string | null>(null);
  const [generateNote, setGenerateNote] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState<string | null>(null);

  // Refetch the gallery; used after a successful upload so the new template
  // appears immediately.
  const refetchTemplates = useCallback(async () => {
    try {
      const res = await apiRequest(`${apiBase}/api/slide-templates/`);
      if (!res.ok) return;
      const data: SlideTemplateInfo[] = await res.json();
      setTemplates(data);
    } catch {
      /* swallow */
    }
  }, [apiBase]);

  const handleDeleteTemplate = useCallback(
    async (template: SlideTemplateInfo) => {
      const ok = window.confirm(
        `Delete "${template.name}"? This removes your uploaded template and all its pages. Existing decks that reference it will break.`
      );
      if (!ok) return;
      setDeletingId(template.id);
      setDeleteError(null);
      try {
        const res = await apiRequest(
          `${apiBase}/api/user-slide-templates/${encodeURIComponent(template.id)}`,
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
        await refetchTemplates();
      } catch (err) {
        setDeleteError(err instanceof Error ? err.message : String(err));
      } finally {
        setDeletingId(null);
      }
    },
    [apiBase, refetchTemplates]
  );

  const handleUploadFile = useCallback(
    async (file: File) => {
      setUploading(true);
      setUploadError(null);
      try {
        const formData = new FormData();
        formData.append("file", file);
        formData.append("name", file.name.replace(/\.pptx$/i, ""));
        const res = await apiRequest(
          `${apiBase}/api/user-slide-templates/upload`,
          { method: "POST", body: formData }
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
        await refetchTemplates();
      } catch (err) {
        setUploadError(err instanceof Error ? err.message : String(err));
      } finally {
        setUploading(false);
        if (fileInputRef.current) fileInputRef.current.value = "";
      }
    },
    [apiBase, refetchTemplates]
  );

  useEffect(() => {
    let cancelled = false;
    const fetchTemplates = async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await apiRequest(`${apiBase}/api/slide-templates/`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data: SlideTemplateInfo[] = await res.json();
        if (!cancelled) setTemplates(data);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void fetchTemplates();
    return () => {
      cancelled = true;
    };
  }, [apiBase]);

  const categories = useMemo(() => {
    const set = new Set<string>();
    templates.forEach((t) => {
      if (t.category) set.add(t.category);
    });
    return ["All", ...Array.from(set).sort()];
  }, [templates]);

  // "Use template" → create a deck bootstrapped from this template, navigate.
  const handleUseTemplate = useCallback(
    async (template: SlideTemplateInfo) => {
      setCreatingFromTemplate(template.id);
      setCreateError(null);
      try {
        const res = await apiRequest(`${apiBase}/api/decks/`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            template_id: template.id,
            title: template.name,
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
        const deck: DeckDetail = await res.json();
        router.push(`/slides/${deck.id}`);
      } catch (err) {
        setCreateError(err instanceof Error ? err.message : String(err));
        setCreatingFromTemplate(null);
      }
    },
    [apiBase, router]
  );

  // POST /api/decks/generate — runs LLM auto-fill (10–30 s) then opens the
  // editor on the new deck.
  const handleGenerate = useCallback(async () => {
    if (!selectedTemplateId || !promptText.trim()) return;
    setGenerating(true);
    setGenerateError(null);
    try {
      const res = await apiRequest(`${apiBase}/api/decks/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          template_id: selectedTemplateId,
          topic: promptText.trim(),
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
      const deck: DeckDetail = await res.json();
      // Phase D5: the backend now returns immediately with
      // generation_status='pending' and runs the LLM in a BackgroundTask.
      // The /generate/{id} route polls until the deck is ready then routes
      // to the editor, matching the PDF page 9 intermediate-task design.
      router.push(`/generate/${deck.id}`);
    } catch (err) {
      setGenerateError(err instanceof Error ? err.message : String(err));
      setGenerating(false);
    }
  }, [apiBase, router, selectedTemplateId, promptText]);

  const filtered = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return templates.filter((t) => {
      const matchCat =
        selectedCategory === "All" || t.category === selectedCategory;
      const matchSearch =
        !q ||
        t.name.toLowerCase().includes(q) ||
        t.description.toLowerCase().includes(q);
      return matchCat && matchSearch;
    });
  }, [templates, selectedCategory, searchQuery]);

  const selectedTemplate = useMemo(
    () => templates.find((t) => t.id === selectedTemplateId) ?? null,
    [templates, selectedTemplateId]
  );

  return (
    <div className="flex h-full flex-col overflow-y-auto bg-background">
      {/* Back-to-home link (matches the PDF) */}
      <div className="mx-auto w-full max-w-6xl px-6 pt-6">
        <Link
          href="/"
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          <ChevronLeft className="h-4 w-4" /> Back to home
        </Link>
      </div>

      {/* Hero — title + subtitle + generate prompt (PDF: AI Slides landing) */}
      <div className="w-full bg-background pb-2 pt-10">
        <div className="mx-auto w-full max-w-3xl px-6">
          <div className="px-2 text-center">
            <h1 className="mb-2 text-3xl font-bold tracking-tight text-foreground">
              AI Slides
            </h1>
            <p className="mb-6 text-[15px] text-muted-foreground">
              Generate a polished deck from a single line — every template
              ships with its own look and feel.
            </p>

            <div className="relative flex w-full items-center gap-1 rounded-full border border-border bg-card pl-4 pr-1 py-1 shadow-sm focus-within:border-primary focus-within:ring-2 focus-within:ring-primary/20">
              <input
                type="text"
                value={promptText}
                onChange={(e) => setPromptText(e.target.value)}
                placeholder="What’s the deck about? e.g. Q2 product roadmap for leadership"
                className="h-10 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground/60"
                disabled={generating}
                onKeyDown={(e) => {
                  if (e.key !== "Enter") return;
                  if (!selectedTemplateId) {
                    setGenerateNote(
                      "Pick a template below first — the AI fills its slots using your prompt."
                    );
                    return;
                  }
                  if (!promptText.trim()) return;
                  void handleGenerate();
                }}
              />
              <button
                type="button"
                onClick={() =>
                  setGenerateNote(
                    "Attaching files to ground the AI is coming with the auto-generate flow. For now, use Upload .pptx below to import a template."
                  )
                }
                title="Attach a file for grounding (coming next)"
                className="rounded-full p-2 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              >
                <Paperclip className="h-4 w-4" />
              </button>
              <Button
                size="sm"
                className="h-9 rounded-full px-4"
                disabled={!promptText.trim() || generating}
                onClick={() => {
                  if (!selectedTemplateId) {
                    setGenerateNote(
                      "Pick a template below first — the AI fills its slots using your prompt."
                    );
                    return;
                  }
                  void handleGenerate();
                }}
              >
                {generating ? (
                  <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Sparkles className="mr-1 h-3.5 w-3.5" />
                )}
                {generating ? "Generating…" : "Generate"}
              </Button>
            </div>

            {selectedTemplate && (
              <div className="mt-3 inline-flex items-center gap-2 rounded-full border border-primary/30 bg-primary/5 px-3 py-1 text-xs text-primary">
                <span>Attached:</span>
                <span className="font-semibold">{selectedTemplate.name}</span>
                <button
                  type="button"
                  onClick={() => setSelectedTemplateId(null)}
                  className="rounded-full p-0.5 hover:bg-primary/10"
                  aria-label="Detach template"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            )}
            {generateNote && (
              <div className="mt-3 flex items-start gap-2 rounded-md border border-amber-300/40 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
                <span className="flex-1 text-left">{generateNote}</span>
                <button
                  onClick={() => setGenerateNote(null)}
                  className="shrink-0 hover:opacity-70"
                  aria-label="Dismiss"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            )}
            {generating && (
              <div className="mt-3 flex items-center gap-2 rounded-md border border-primary/30 bg-primary/5 px-3 py-2 text-xs text-primary">
                <Loader2 className="h-3 w-3 animate-spin" />
                <span className="flex-1 text-left">
                  Drafting your deck with AI — this usually takes 10–30 seconds…
                </span>
              </div>
            )}
            {generateError && (
              <div className="mt-3 flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
                <span className="flex-1 text-left">
                  Generate failed: {generateError}
                </span>
                <button
                  onClick={() => setGenerateError(null)}
                  className="shrink-0 text-destructive hover:opacity-70"
                  aria-label="Dismiss"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            )}
            <input
              ref={fileInputRef}
              type="file"
              accept=".pptx,application/vnd.openxmlformats-officedocument.presentationml.presentation"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void handleUploadFile(f);
              }}
            />
            {uploading && (
              <div className="mt-3 inline-flex items-center gap-2 text-xs text-muted-foreground">
                <Loader2 className="h-3 w-3 animate-spin" /> Importing your file…
              </div>
            )}
            {uploadError && (
              <div className="mt-3 flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
                <span className="flex-1 text-left">Upload failed: {uploadError}</span>
                <button
                  onClick={() => setUploadError(null)}
                  className="shrink-0 text-destructive hover:opacity-70"
                  aria-label="Dismiss"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Templates header row — PDF page 6: label on the left, category pills
          flowing to the right on the same line; secondary tools (search +
          upload) live in a smaller utility row below. */}
      <div className="mx-auto w-full max-w-6xl px-6 pt-10">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold tracking-tight text-foreground">
              Templates
            </h2>
            <p className="text-xs text-muted-foreground">
              Pick one to get a head start.
            </p>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-1.5">
            {categories.map((c) => (
              <button
                key={c}
                onClick={() => setSelectedCategory(c)}
                className={cn(
                  "h-7 rounded-full px-3 text-xs font-medium transition",
                  selectedCategory === c
                    ? "bg-foreground text-background"
                    : "bg-muted text-muted-foreground hover:bg-muted/80"
                )}
              >
                {c}
              </button>
            ))}
          </div>
        </div>
        <div className="flex items-center justify-between gap-2 border-t border-border/40 pt-3">
          <div className="relative w-64 max-w-full">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search templates…"
              className={cn(
                "h-9 w-full rounded-full border border-border bg-card pl-8 pr-3 text-xs",
                "outline-none transition placeholder:text-muted-foreground/60",
                "focus:border-primary focus:ring-2 focus:ring-primary/20"
              )}
            />
          </div>
          <Button
            variant="outline"
            size="sm"
            className="h-9 shrink-0 rounded-full px-3 text-xs"
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading}
            title="Import a .pptx as a new template"
          >
            {uploading ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Upload className="mr-1.5 h-3.5 w-3.5" />
            )}
            {uploading ? "Importing…" : "Upload .pptx"}
          </Button>
        </div>
      </div>

      {/* Grid */}
      <div className="mx-auto w-full max-w-6xl flex-1 px-6 pb-16 pt-6">
        {createError && (
          <div className="mb-4 rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
            Could not create deck: {createError}
          </div>
        )}
        {deleteError && (
          <div className="mb-4 rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
            Could not delete template: {deleteError}
          </div>
        )}
        {loading ? (
          <div className="flex h-64 items-center justify-center text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            Loading templates…
          </div>
        ) : error ? (
          <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-6 text-sm text-destructive">
            Could not load templates: {error}
          </div>
        ) : filtered.length === 0 ? (
          <div className="rounded-lg border border-dashed p-12 text-center text-sm text-muted-foreground">
            No templates match those filters.
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {filtered.map((t) => (
              <SlideTemplateCard
                key={t.id}
                template={t}
                apiBase={apiBase}
                busy={creatingFromTemplate === t.id}
                deleting={deletingId === t.id}
                selected={selectedTemplateId === t.id}
                onSelect={() =>
                  setSelectedTemplateId((cur) => (cur === t.id ? null : t.id))
                }
                onUse={() => void handleUseTemplate(t)}
                onDelete={
                  t.is_user_uploaded
                    ? () => void handleDeleteTemplate(t)
                    : undefined
                }
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function SlideTemplateCard({
  template,
  apiBase,
  busy,
  deleting,
  selected,
  onUse,
  onSelect,
  onDelete,
}: {
  template: SlideTemplateInfo;
  apiBase: string;
  busy: boolean;
  deleting: boolean;
  selected: boolean;
  onUse: () => void;
  onSelect: () => void;
  onDelete?: () => void;
}) {
  const coverUrl = template.thumbnail_urls[0]
    ? `${apiBase}${template.thumbnail_urls[0]}`
    : undefined;

  return (
    <div
      className={cn(
        "group relative flex flex-col overflow-hidden rounded-xl border bg-card text-left",
        "transition hover:shadow-md cursor-pointer",
        selected
          ? "border-primary ring-2 ring-primary/30"
          : "border-border hover:border-primary/40",
        (busy || deleting) && "opacity-60"
      )}
      onClick={(e) => {
        // Stop clicks that came from inner buttons (delete / Use button).
        const target = e.target as HTMLElement;
        if (target.closest("button")) return;
        onSelect();
      }}
    >
      {onDelete && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
          disabled={deleting}
          className={cn(
            "absolute right-2 top-2 z-10 flex h-7 w-7 items-center justify-center rounded-md",
            "bg-background/85 text-muted-foreground backdrop-blur opacity-0",
            "transition hover:bg-destructive hover:text-destructive-foreground group-hover:opacity-100"
          )}
          aria-label={`Delete ${template.name}`}
          title="Delete this uploaded template"
        >
          {deleting ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Trash2 className="h-3.5 w-3.5" />
          )}
        </button>
      )}
      <div className="relative aspect-[16/9] w-full overflow-hidden bg-muted">
        {coverUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={coverUrl}
            alt={template.name}
            className="h-full w-full object-cover transition group-hover:scale-[1.02]"
          />
        ) : (
          <div className="flex h-full items-center justify-center text-muted-foreground">
            <Layers className="h-8 w-8 opacity-40" />
          </div>
        )}
        {template.category && (
          <Badge
            variant="secondary"
            className="absolute left-3 top-3 bg-background/85 backdrop-blur"
          >
            {template.category}
          </Badge>
        )}
        {selected && (
          <div className="absolute inset-x-0 bottom-0 flex items-center justify-between bg-primary/90 px-3 py-2 text-xs font-medium text-primary-foreground backdrop-blur">
            <span className="flex items-center gap-1">
              <Sparkles className="h-3 w-3" /> Selected
            </span>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onUse();
              }}
              disabled={busy || deleting}
              className="rounded-full bg-background/95 px-2.5 py-1 text-[11px] font-semibold text-foreground hover:bg-background disabled:opacity-60"
              title="Skip AI and start with empty slots"
            >
              {busy ? (
                <>
                  <Loader2 className="mr-1 inline-block h-3 w-3 animate-spin" />
                  Opening…
                </>
              ) : (
                <>Use without AI →</>
              )}
            </button>
          </div>
        )}
      </div>
      <div className="flex flex-1 flex-col gap-2 px-4 py-3">
        <div className="flex items-center justify-between">
          <span className="font-semibold text-foreground">{template.name}</span>
          <span className="text-xs text-muted-foreground">
            {template.page_count} pages
          </span>
        </div>
        <p className="line-clamp-2 text-sm text-muted-foreground">
          {template.description}
        </p>
        {!selected && (
          <div className="mt-auto flex items-center gap-1 pt-2 text-xs font-medium text-primary opacity-0 transition group-hover:opacity-100">
            <ChevronRight className="h-3 w-3" />
            Tap to select
          </div>
        )}
      </div>
    </div>
  );
}

