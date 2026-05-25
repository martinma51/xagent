"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Search, Layers, ChevronRight, Loader2 } from "lucide-react";

import { apiRequest } from "@/lib/api-wrapper";
import { cn, getApiUrl } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import type { SlideTemplateInfo } from "@/types/slide_template";

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

  return (
    <div className="flex h-full flex-col overflow-y-auto bg-background">
      {/* Hero */}
      <div className="w-full border-b border-border/60 bg-background pb-8 pt-10">
        <div className="mx-auto flex max-w-4xl flex-col items-center px-6 text-center">
          <h1 className="mb-2 text-3xl font-bold tracking-tight text-foreground">
            Slide Templates
          </h1>
          <p className="mb-6 text-[15px] text-muted-foreground">
            Browse a curated set of ready-made decks, fill in your content, and
            export to PowerPoint.
          </p>
          <div className="relative w-full max-w-xl">
            <Search className="pointer-events-none absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search templates…"
              className={cn(
                "h-11 w-full rounded-full border border-border bg-card pl-11 pr-4 text-sm",
                "outline-none transition placeholder:text-muted-foreground/60",
                "focus:border-primary focus:ring-2 focus:ring-primary/20"
              )}
            />
          </div>
        </div>
      </div>

      {/* Category pills */}
      <div className="mx-auto w-full max-w-6xl px-6 pt-6">
        <div className="flex flex-wrap gap-2">
          {categories.map((c) => (
            <button
              key={c}
              onClick={() => setSelectedCategory(c)}
              className={cn(
                "h-8 rounded-full px-3 text-xs font-medium transition",
                selectedCategory === c
                  ? "bg-primary text-primary-foreground"
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
                onUse={() => router.push(`/slides/${encodeURIComponent(t.id)}`)}
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
  onUse,
}: {
  template: SlideTemplateInfo;
  apiBase: string;
  onUse: () => void;
}) {
  const coverUrl = template.thumbnail_urls[0]
    ? `${apiBase}${template.thumbnail_urls[0]}`
    : undefined;

  return (
    <button
      onClick={onUse}
      className={cn(
        "group flex flex-col overflow-hidden rounded-xl border border-border bg-card text-left",
        "transition hover:border-primary/40 hover:shadow-md"
      )}
    >
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
        <div className="mt-auto flex items-center gap-1 pt-2 text-xs font-medium text-primary opacity-0 transition group-hover:opacity-100">
          Use template
          <ChevronRight className="h-3 w-3" />
        </div>
      </div>
    </button>
  );
}

