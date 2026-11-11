import { useState } from "react";

import type { PolicyCitationsResponse } from "../../../api/types";

export function PolicyCitationsDrawer({
  citations,
}: {
  citations: PolicyCitationsResponse | null;
}) {
  const [open, setOpen] = useState(false);
  const topics = citations ? Object.entries(citations.citations) : [];

  return (
    <section className="panel" data-testid="policy-citations-drawer">
      <div className="page-header">
        <h3>Policy citations</h3>
        <button
          type="button"
          className="btn btn-ghost"
          onClick={() => setOpen((value) => !value)}
        >
          {open ? "Hide" : "Show"} drawer
        </button>
      </div>
      {open && (
        <div className="citations-drawer">
          {!citations || topics.length === 0 ? (
            <p className="muted">No policy citations available.</p>
          ) : (
            topics.map(([topic, chunks]) => (
              <div key={topic} className="citation-topic">
                <h4>{topic}</h4>
                {chunks.map((chunk) => (
                  <blockquote key={chunk.chunk_id}>
                    <strong>{chunk.section_label}</strong>
                    <p>{chunk.content_text}</p>
                    <span className="muted">
                      rank {chunk.rank_score.toFixed(3)}
                    </span>
                  </blockquote>
                ))}
              </div>
            ))
          )}
        </div>
      )}
    </section>
  );
}
