// Slide template types — mirror the Pydantic models on the backend at
// src/xagent/web/api/slide_templates.py.

export interface SlotSpec {
  type: string;
  required: boolean;
  max_length?: number | null;
  hint?: string | null;
  /** Set to "manual" when the slot was drawn on by the user (Phase C'). */
  origin?: "manual" | string | null;
  /** Canvas-space rectangle for slots added via drawing mode. */
  rect?: { x: number; y: number; width: number; height: number } | null;
  default_text?: string | null;
}

export interface SlidePageInfo {
  idx: number;
  layout: string;
  file: string;
  slots: Record<string, SlotSpec>;
}

export interface SlideTemplateInfo {
  id: string;
  name: string;
  description: string;
  category: string;
  page_count: number;
  thumbnail_urls: string[];
  is_user_uploaded?: boolean;
}

export interface SlideTemplateDetail extends SlideTemplateInfo {
  version?: string | null;
  canvas: Record<string, number>;
  pages: SlidePageInfo[];
}

/** Map from page index → { slot name → text value }. */
export type DeckSlotValues = Record<number, Record<string, string>>;

export interface RenderDeckRequest {
  slot_values: DeckSlotValues;
  filename?: string;
}
