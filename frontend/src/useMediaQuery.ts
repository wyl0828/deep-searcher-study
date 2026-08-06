import { useEffect, useState } from "react";

function matchesMediaQuery(query: string) {
  return typeof window !== "undefined" &&
    typeof window.matchMedia === "function"
    ? window.matchMedia(query).matches
    : false;
}

export function useMediaQuery(query: string) {
  const [matches, setMatches] = useState(() => matchesMediaQuery(query));

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return undefined;
    const mediaQuery = window.matchMedia(query);
    const handleChange = () => setMatches(mediaQuery.matches);

    handleChange();
    mediaQuery.addEventListener("change", handleChange);
    return () => mediaQuery.removeEventListener("change", handleChange);
  }, [query]);

  return matches;
}
