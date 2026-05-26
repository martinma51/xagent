"use client";

import { useCallback, useRef, useState } from "react";
import {
  ChevronLeft,
  Download,
  Loader2,
  Paperclip,
  RefreshCw,
  Search,
  Sparkles,
  X,
  ImagePlus,
} from "lucide-react";
import Link from "next/link";

import { apiRequest } from "@/lib/api-wrapper";
import { cn, getApiUrl } from "@/lib/utils";
import { Button } from "@/components/ui/button";

interface GeneratedImage {
  url: string;
  seed: number;
  prompt: string;
  failed?: boolean;
}

// Placeholder style palette — same set every render, so we hoist it out of
// the component to keep useCallback deps clean.
const STYLES: {
  id: string;
  name: string;
  category: string;
  description: string;
  gradient: string;
}[] = [
  {
    id: "studio-portrait",
    name: "Studio Portrait",
    category: "Portrait",
    description:
      "Studio portrait, even softbox lighting, neutral backdrop, sharp focus on the subject",
    gradient: "linear-gradient(135deg, #2c3e50, #4a6fa5)",
  },
  {
    id: "editorial-product",
    name: "Editorial Product",
    category: "Product",
    description:
      "Bright magazine-cover product shot with airy negative space and clean shadow",
    gradient: "linear-gradient(135deg, #ffe0b2, #ffab91)",
  },
  {
    id: "isometric-illustration",
    name: "Isometric Illustration",
    category: "Illustration",
    description:
      "Isometric vector illustration with pastel colors, geometric 3/4-view, soft shadows",
    gradient: "linear-gradient(135deg, #c4b5fd, #818cf8)",
  },
  {
    id: "watercolor-mood",
    name: "Watercolor Mood",
    category: "Illustration",
    description:
      "Loose watercolour painting, wet-on-wet washes, hand-drawn ink linework",
    gradient: "linear-gradient(135deg, #fbcfe8, #c7d2fe)",
  },
  {
    id: "cinematic-still",
    name: "Cinematic Still",
    category: "Photo",
    description:
      "Cinematic still, anamorphic widescreen frame, motivated lighting, subtle film grain",
    gradient: "linear-gradient(135deg, #1f2937, #ef4444)",
  },
  {
    id: "minimal-brand-mark",
    name: "Minimal Brand Mark",
    category: "Brand",
    description:
      "Minimal single-colour geometric brand mark, flat vector, suitable for an app icon",
    gradient: "linear-gradient(135deg, #d1fae5, #34d399)",
  },
  {
    id: "social-card",
    name: "Social Card",
    category: "Social",
    description:
      "Bold typographic social card, OG-image sized, with playful colour blocking",
    gradient: "linear-gradient(135deg, #fde68a, #fb7185)",
  },
  {
    id: "data-poster",
    name: "Data Poster",
    category: "Brand",
    description:
      "Editorial data poster, large single statistic, thin gold rule, lots of whitespace",
    gradient: "linear-gradient(135deg, #e2e8f0, #94a3b8)",
  },
];

/**
 * AI Images landing — shell only (D3).
 *
 * The PDF (page 2) calls out AI Slides and AI Images as the first two
 * widgets to ship. This page mirrors the /slides landing layout — title,
 * Generate prompt with file attach + drag-drop, category pills, template
 * grid — but the backend that turns a prompt into images is still TBD, so
 * Generate currently surfaces a "Coming soon" notice instead of calling an
 * endpoint. Wiring it to an image model lives in a follow-up phase.
 */
export default function ImagesLandingPage() {
  const apiBase = getApiUrl();
  const [promptText, setPromptText] = useState("");
  const [selectedStyleId, setSelectedStyleId] = useState<string | null>(null);
  const [generateNote, setGenerateNote] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedCategory, setSelectedCategory] = useState("All");
  const [attachedFiles, setAttachedFiles] = useState<File[]>([]);
  const [isDragOver, setIsDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState<string | null>(null);
  const [results, setResults] = useState<GeneratedImage[]>([]);
  const [lastPrompt, setLastPrompt] = useState<string>("");

  const addAttachedFiles = useCallback((incoming: FileList | File[] | null) => {
    if (!incoming) return;
    const arr = Array.from(incoming);
    if (arr.length === 0) return;
    setAttachedFiles((prev) => {
      const seen = new Set(prev.map((f) => `${f.name}:${f.size}`));
      return [...prev, ...arr.filter((f) => !seen.has(`${f.name}:${f.size}`))];
    });
  }, []);

  const removeAttachedFile = (idx: number) => {
    setAttachedFiles((prev) => prev.filter((_, i) => i !== idx));
  };

  const styles = STYLES;
  const categories = [
    "All",
    ...Array.from(new Set(styles.map((s) => s.category))).sort(),
  ];

  const filtered = styles.filter((s) => {
    const q = searchQuery.trim().toLowerCase();
    return (
      (selectedCategory === "All" || s.category === selectedCategory) &&
      (!q ||
        s.name.toLowerCase().includes(q) ||
        s.description.toLowerCase().includes(q))
    );
  });

  const formatBytes = (n: number) => {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / 1024 / 1024).toFixed(1)} MB`;
  };

  const handleGenerate = useCallback(async () => {
    if (!promptText.trim()) return;
    setGenerating(true);
    setGenerateError(null);
    setGenerateNote(null);
    const style = STYLES.find((s) => s.id === selectedStyleId);
    const finalPrompt = promptText.trim();
    try {
      const res = await apiRequest(`${apiBase}/api/images/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          prompt: finalPrompt,
          style_description: style?.description ?? null,
          n: 4,
          width: 1024,
          height: 1024,
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
      const body = (await res.json()) as { images: GeneratedImage[] };
      setResults(body.images);
      setLastPrompt(finalPrompt);
    } catch (err) {
      setGenerateError(err instanceof Error ? err.message : String(err));
    } finally {
      setGenerating(false);
    }
  }, [apiBase, promptText, selectedStyleId]);

  const handleDownload = useCallback(async (img: GeneratedImage, idx: number) => {
    try {
      const res = await fetch(img.url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `ai-image-${img.seed}.png`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setGenerateError(
        `Couldn't download image #${idx + 1}: ${err instanceof Error ? err.message : err}`
      );
    }
  }, []);

  return (
    <div className="flex h-full flex-col overflow-y-auto bg-background">
      {/* Back to home */}
      <div className="mx-auto w-full max-w-6xl px-6 pt-6">
        <Link
          href="/"
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          <ChevronLeft className="h-4 w-4" /> Back to home
        </Link>
      </div>

      {/* Hero */}
      <div className="w-full bg-background pb-2 pt-8">
        <div className="mx-auto w-full max-w-3xl px-6">
          <div
            className={cn(
              "rounded-2xl border bg-muted/40 px-8 py-10 text-center transition-colors",
              isDragOver
                ? "border-primary border-dashed bg-primary/5"
                : "border-border"
            )}
            onDragOver={(e) => {
              if (!e.dataTransfer?.types.includes("Files")) return;
              e.preventDefault();
              setIsDragOver(true);
            }}
            onDragLeave={(e) => {
              if (e.currentTarget.contains(e.relatedTarget as Node)) return;
              setIsDragOver(false);
            }}
            onDrop={(e) => {
              if (!e.dataTransfer?.files?.length) return;
              e.preventDefault();
              setIsDragOver(false);
              addAttachedFiles(e.dataTransfer.files);
            }}
          >
            <h1 className="mb-2 text-3xl font-bold tracking-tight text-foreground">
              AI Images
            </h1>
            <p className="mb-6 text-[15px] text-muted-foreground">
              Describe what you want, pick a look, and generate ready-to-use
              imagery.
            </p>

            <div className="relative flex w-full items-center gap-1 rounded-full border border-border bg-card pl-4 pr-1 py-1 shadow-sm focus-within:border-primary focus-within:ring-2 focus-within:ring-primary/20">
              <input
                type="text"
                value={promptText}
                onChange={(e) => setPromptText(e.target.value)}
                placeholder="What should the image look like? e.g. minimalist isometric of a developer at a standing desk"
                className="h-10 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground/60"
                disabled={generating}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && promptText.trim() && !generating) {
                    void handleGenerate();
                  }
                }}
              />
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                title="Attach reference images (coming next)"
                className="rounded-full p-2 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              >
                <Paperclip className="h-4 w-4" />
              </button>
              <Button
                size="sm"
                className="h-9 rounded-full px-4"
                disabled={!promptText.trim() || generating}
                onClick={() => void handleGenerate()}
              >
                {generating ? (
                  <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Sparkles className="mr-1 h-3.5 w-3.5" />
                )}
                {generating ? "Generating…" : "Generate"}
              </Button>
            </div>

            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              multiple
              className="hidden"
              onChange={(e) => {
                addAttachedFiles(e.target.files);
                if (fileInputRef.current) fileInputRef.current.value = "";
              }}
            />

            {attachedFiles.length > 0 && (
              <div className="mt-3 flex flex-wrap justify-center gap-1.5">
                {attachedFiles.map((f, i) => (
                  <span
                    key={`${f.name}-${i}`}
                    className="inline-flex items-center gap-1.5 rounded-full bg-muted px-2.5 py-1 text-[12px] text-foreground"
                    title={`${f.name} · ${formatBytes(f.size)}`}
                  >
                    <ImagePlus className="h-3 w-3 shrink-0 text-muted-foreground" />
                    <span className="max-w-[180px] truncate">{f.name}</span>
                    <span className="text-[10px] text-muted-foreground">
                      {formatBytes(f.size)}
                    </span>
                    <button
                      type="button"
                      onClick={() => removeAttachedFile(i)}
                      className="rounded-full p-0.5 hover:bg-foreground/10"
                      aria-label={`Remove ${f.name}`}
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </span>
                ))}
              </div>
            )}

            {selectedStyleId && (
              <div className="mt-3 inline-flex items-center gap-2 rounded-full border border-primary/30 bg-primary/5 px-3 py-1 text-xs text-primary">
                <span>Attached:</span>
                <span className="font-semibold">
                  {styles.find((s) => s.id === selectedStyleId)?.name}
                </span>
                <button
                  type="button"
                  onClick={() => setSelectedStyleId(null)}
                  className="rounded-full p-0.5 hover:bg-primary/10"
                  aria-label="Detach style"
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
                  Pollinating pixels… the first image usually appears in
                  5–10&nbsp;seconds.
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
            {isDragOver && (
              <div className="mt-3 text-xs text-primary">
                Drop reference images to attach
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Results gallery — shown right after a successful generate */}
      {results.length > 0 && (
        <div className="mx-auto w-full max-w-6xl px-6 pt-8">
          <div className="mb-3 flex items-end justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold tracking-tight text-foreground">
                Latest generations
              </h2>
              <p className="text-xs text-muted-foreground">
                Prompt: <span className="italic">“{lastPrompt}”</span>
              </p>
            </div>
            <Button
              size="sm"
              variant="outline"
              className="h-9 rounded-full px-3 text-xs"
              onClick={() => void handleGenerate()}
              disabled={generating}
              title="Re-roll with fresh seeds"
            >
              {generating ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              ) : (
                <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
              )}
              Regenerate
            </Button>
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {results.map((img, i) => (
              <div
                key={`${img.seed}`}
                className="group relative aspect-square overflow-hidden rounded-xl border border-border bg-muted"
              >
                {img.failed ? (
                  <div className="flex h-full w-full flex-col items-center justify-center gap-2 p-4 text-center">
                    <ImagePlus className="h-6 w-6 text-muted-foreground/60" />
                    <p className="text-xs font-medium text-muted-foreground">
                      Image service unreachable
                    </p>
                    <p className="text-[11px] text-muted-foreground/70">
                      pollinations.ai didn’t respond — likely a network
                      restriction.
                    </p>
                  </div>
                ) : (
                  <>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={img.url}
                      alt={`Generated image ${i + 1}`}
                      loading="lazy"
                      className="h-full w-full object-cover"
                      onError={() => {
                        setResults((prev) =>
                          prev.map((r, j) =>
                            j === i ? { ...r, failed: true } : r
                          )
                        );
                      }}
                    />
                    <div className="pointer-events-none absolute inset-0 flex items-end justify-end p-2 opacity-0 transition-opacity group-hover:opacity-100">
                      <button
                        type="button"
                        onClick={() => void handleDownload(img, i)}
                        className="pointer-events-auto flex items-center gap-1.5 rounded-full bg-background/95 px-3 py-1.5 text-xs font-medium text-foreground shadow hover:bg-background"
                        title="Download this image"
                      >
                        <Download className="h-3 w-3" />
                        Download
                      </button>
                    </div>
                  </>
                )}
                <span className="absolute left-2 top-2 rounded bg-background/80 px-1.5 py-0.5 text-[10px] font-mono text-muted-foreground backdrop-blur">
                  seed {img.seed}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Templates header */}
      <div className="mx-auto w-full max-w-6xl px-6 pt-8">
        <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold tracking-tight text-foreground">
              Styles
            </h2>
            <p className="text-xs text-muted-foreground">
              Pick a look — the description gets prepended to your prompt.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <div className="relative w-64">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search styles…"
                className={cn(
                  "h-9 w-full rounded-full border border-border bg-card pl-8 pr-3 text-xs",
                  "outline-none transition placeholder:text-muted-foreground/60",
                  "focus:border-primary focus:ring-2 focus:ring-primary/20"
                )}
              />
            </div>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {categories.map((c) => (
            <button
              key={c}
              onClick={() => setSelectedCategory(c)}
              className={cn(
                "h-8 rounded-full px-3 text-xs font-medium transition",
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

      {/* Grid */}
      <div className="mx-auto w-full max-w-6xl flex-1 px-6 pb-16 pt-6">
        <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {filtered.map((s) => {
            const isSelected = selectedStyleId === s.id;
            return (
              <div
                key={s.id}
                onClick={() =>
                  setSelectedStyleId((cur) => (cur === s.id ? null : s.id))
                }
                className={cn(
                  "group relative flex flex-col overflow-hidden rounded-xl border bg-card text-left transition hover:shadow-md cursor-pointer",
                  isSelected
                    ? "border-primary ring-2 ring-primary/30"
                    : "border-border hover:border-primary/40"
                )}
              >
                <div
                  className="aspect-square w-full"
                  style={{ background: s.gradient }}
                  aria-hidden
                />
                <div className="flex flex-1 flex-col gap-1 px-4 py-3">
                  <div className="flex items-center justify-between">
                    <span className="font-semibold text-foreground">{s.name}</span>
                    <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
                      {s.category}
                    </span>
                  </div>
                  <p className="line-clamp-2 text-sm text-muted-foreground">
                    {s.description}
                  </p>
                  {isSelected && (
                    <div className="mt-2 flex items-center gap-1 text-xs font-medium text-primary">
                      <Sparkles className="h-3 w-3" /> Selected
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
