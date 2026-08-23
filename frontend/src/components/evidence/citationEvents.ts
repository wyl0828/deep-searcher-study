export const TOGGLE_CITATIONS_EVENT = "deepsearcher:toggle-citations";
export const CITATION_STATE_EVENT = "deepsearcher:citation-state";

export type CitationDrawerState = {
  available: boolean;
  open: boolean;
};
