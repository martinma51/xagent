// Slide deck + layout types — mirror the Pydantic models in
// src/xagent/web/api/decks.py and src/xagent/web/api/slide_layouts.py.

import type { SlotSpec } from "./slide_template";

/** One layout = one page of one template, globally addressable. */
export interface SlideLayoutInfo {
  id: string; // "<template_id>:<page_idx>"
  template_id: string;
  template_name: string;
  template_category: string;
  page_idx: number;
  layout: string;
  thumbnail_url: string;
  slots: Record<string, SlotSpec>;
  /** True when this layout came from a template the current user uploaded. */
  is_user_uploaded?: boolean;
}

/** One page of a deck: a layout reference + the user's filled slot values. */
export interface DeckPageEntry {
  layout_id: string;
  slot_values: Record<string, string>;
}

export interface DeckInfo {
  id: number;
  template_id: string;
  title: string;
  topic: string;
  page_count: number;
  created_at: string;
  updated_at: string;
}

export interface DeckDetail extends DeckInfo {
  pages: DeckPageEntry[];
}

export interface DeckCreateRequest {
  template_id?: string;
  title?: string;
  topic?: string;
  pages?: DeckPageEntry[];
}

export interface DeckUpdateRequest {
  title?: string;
  topic?: string;
  pages?: DeckPageEntry[];
}
