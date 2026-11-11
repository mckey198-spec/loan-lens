import type { AiObservation } from "../../../api/types";
import { AdvisoryBanner } from "../../../components/AdvisoryBanner";

export function AiObservationsPanel({
  observation,
}: {
  observation: AiObservation | null;
}) {
  return (
    <section className="panel" data-testid="ai-observations-panel">
      <h3>Advisory AI observations</h3>
      <AdvisoryBanner />
      {!observation ? (
        <p className="muted">No AI observation for this application.</p>
      ) : (
        <>
          <p
            className="ai-generated-tag"
            data-testid="is-ai-generated-indicator"
          >
            is_ai_generated: {String(observation.is_ai_generated)}
          </p>
          <p>{observation.observation_text}</p>
          {observation.risk_factors.length > 0 && (
            <>
              <h4>Risk factors</h4>
              <ul>
                {observation.risk_factors.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </>
          )}
          {observation.strengths.length > 0 && (
            <>
              <h4>Strengths</h4>
              <ul>
                {observation.strengths.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </>
          )}
        </>
      )}
    </section>
  );
}
