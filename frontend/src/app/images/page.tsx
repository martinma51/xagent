"use client";

import { useCallback, useRef, useState } from "react";
import {
  ChevronLeft,
  Paperclip,
  Search,
  Sparkles,
  X,
  ImagePlus,
} from "lucide-react";
import Link from "next/link";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

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
  const [promptText, setPromptText] = useState("");
  const [selectedStyleId, setSelectedStyleId] = useState<string | null>(null);
  const [generateNote, setGenerateNote] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedCategory, setSelectedCategory] = useState("All");
  const [attachedFiles, setAttachedFiles] = useState<File[]>([]);
  const [isDragOver, setIsDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

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

  // Placeholder style palette — replace with real templates once the image
  // backend ships. Each gradient hints at the visual identity it would
  // produce so the page reads as "AI Images" not "AI Slides".
  const styles: {
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
        "Even softbox lighting, neutral backdrop, sharp focus on the subject.",
      gradient: "linear-gradient(135deg, #2c3e50, #4a6fa5)",
    },
    {
      id: "editorial-product",
      name: "Editorial Product",
      category: "Product",
      description:
        "Bright magazine-cover product shot with airy negative space.",
      gradient: "linear-gradient(135deg, #ffe0b2, #ffab91)",
    },
    {
      id: "isometric-illustration",
      name: "Isometric Illustration",
      category: "Illustration",
      description:
        "Geometric 3/4-view vector scenes built from pastel blocks.",
      gradient: "linear-gradient(135deg, #c4b5fd, #818cf8)",
    },
    {
      id: "watercolor-mood",
      name: "Watercolor Mood",
      category: "Illustration",
      description: "Loose, wet-on-wet washes with hand-drawn ink linework.",
      gradient: "linear-gradient(135deg, #fbcfe8, #c7d2fe)",
    },
    {
      id: "cinematic-still",
      name: "Cinematic Still",
      category: "Photo",
      description: "Anamorphic widescreen frame, motivated lighting, film grain.",
      gradient: "linear-gradient(135deg, #1f2937, #ef4444)",
    },
    {
      id: "minimal-brand-mark",
      name: "Minimal Brand Mark",
      category: "Brand",
      description: "Single-colour geometric mark suitable for app icons.",
      gradient: "linear-gradient(135deg, #d1fae5, #34d399)",
    },
    {
      id: "social-card",
      name: "Social Card",
      category: "Social",
      description: "Bold typographic OG-style image sized for sharing.",
      gradient: "linear-gradient(135deg, #fde68a, #fb7185)",
    },
    {
      id: "data-poster",
      name: "Data Poster",
      category: "Brand",
      description: "Editorial-style hero with one statistic and a thin rule.",
      gradient: "linear-gradient(135deg, #e2e8f0, #94a3b8)",
    },
  ];

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

  const handleGenerate = () => {
    setGenerateNote(
      "AI Image generation isn’t wired up yet. The styles below show the look you’ll be able to dial in; the model integration lands in a follow-up phase."
    );
  };

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
                onKeyDown={(e) => {
                  if (e.key === "Enter" && promptText.trim()) handleGenerate();
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
                disabled={!promptText.trim()}
                onClick={handleGenerate}
              >
                <Sparkles className="mr-1 h-3.5 w-3.5" />
                Generate
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
            {isDragOver && (
              <div className="mt-3 text-xs text-primary">
                Drop reference images to attach
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Templates header */}
      <div className="mx-auto w-full max-w-6xl px-6 pt-8">
        <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold tracking-tight text-foreground">
              Styles
            </h2>
            <p className="text-xs text-muted-foreground">
              Pick a look to start. Backend generation lands soon.
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
        <p className="mt-8 text-center text-xs text-muted-foreground">
          Generation isn’t wired up yet. The styles above are placeholders for
          what the backend will produce.
        </p>
      </div>
    </div>
  );
}
