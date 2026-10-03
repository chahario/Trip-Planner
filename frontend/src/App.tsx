import { useCallback, useEffect, useRef, useState } from "react";
import PlannerForm from "./components/PlannerForm";
import TracePanel from "./components/TracePanel";
import PlanView from "./components/PlanView";
import Clarify from "./components/Clarify";
import SavePlanBar from "./components/SavePlanBar";
import SavedPlans from "./components/SavedPlans";
import BookingLinks from "./components/BookingLinks";
import WeatherBar from "./components/WeatherBar";
import ClarifyPanel from "./components/ClarifyPanel";
import Hotels from "./components/Hotels";
import AuthModal from "./components/AuthModal";
import { streamPlan, fetchClarify, fetchUsage } from "./lib/api";
import { getAuth, logout } from "./lib/auth";
import {
  deleteSavedPlan,
  listSavedPlans,
  savePlan,
  updateSavedPlan,
} from "./lib/savedApi";
import { getUserToken, setUserToken } from "./lib/user";
import type {
  Plan,
  PlanRequest,
  SavedPlan,
  TraceStep,
  ClarifyingQuestion,
  Weather,
  Usage,
} from "./lib/types";

export default function App() {
  const [trace, setTrace] = useState<TraceStep[]>([]);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [weather, setWeather] = useState<Weather | null>(null);
  // Per-city weather outlooks for multi-city trips.
  const [weatherByCity, setWeatherByCity] = useState<Weather[]>([]);
  // Day-by-day weather (when a start date was given).
  const [weatherByDay, setWeatherByDay] = useState<Weather[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [questions, setQuestions] = useState<ClarifyingQuestion[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  // current request (stored alongside a saved plan)
  const [currentRequest, setCurrentRequest] = useState<PlanRequest | null>(null);

  // saved-plan library
  const [userToken, setToken] = useState<string>(() => getUserToken());
  const [savedPlans, setSavedPlans] = useState<SavedPlan[]>([]);
  const [savedLoading, setSavedLoading] = useState(false);
  const [activeSavedId, setActiveSavedId] = useState<string | null>(null);
  const [seedTitle, setSeedTitle] = useState("");
  const [seedNotes, setSeedNotes] = useState("");
  const [saving, setSaving] = useState(false);
  const [planNonce, setPlanNonce] = useState(0);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);
  const [formCollapsed, setFormCollapsed] = useState(false);

  // ask-first clarifying flow
  const [pendingReq, setPendingReq] = useState<PlanRequest | null>(null);
  const [clarifyQuestions, setClarifyQuestions] = useState<ClarifyingQuestion[]>([]);
  const [clarifyLoading, setClarifyLoading] = useState(false);
  const [clarifySource, setClarifySource] = useState("ai");
  const [asking, setAsking] = useState(false);

  // auth
  const [authEmail, setAuthEmail] = useState<string | null>(() => getAuth()?.email ?? null);
  const [showAuth, setShowAuth] = useState(false);

  // trip quota (N of 10 per window)
  const [usage, setUsage] = useState<Usage | null>(null);

  const refreshSaved = useCallback(async () => {
    setSavedLoading(true);
    try {
      setSavedPlans(await listSavedPlans());
    } catch {
      /* non-fatal — library just stays empty */
    } finally {
      setSavedLoading(false);
    }
  }, []);

  const refreshUsage = useCallback(async () => {
    setUsage(await fetchUsage());
  }, []);

  useEffect(() => {
    refreshSaved();
    refreshUsage();
  }, [refreshSaved, refreshUsage, userToken, authEmail]);

  const handleLogout = useCallback(async () => {
    await logout();
    setAuthEmail(null);
    setActiveSavedId(null);
  }, []);

  // Actually run the plan (after the ask-first step).
  const runPlan = useCallback((req: PlanRequest) => {
    setAsking(false);
    setClarifyQuestions([]);
    setTrace([]);
    setPlan(null);
    setWeather(null);
    setWeatherByCity([]);
    setWeatherByDay([]);
    setWarnings([]);
    setQuestions([]);
    setError(null);
    setRunning(true);
    setCurrentRequest(req);
    setActiveSavedId(null);
    setSeedTitle("");
    setSeedNotes("");
    setSaveMsg(null);
    setPlanNonce((n) => n + 1);

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    // Capture the request so the auto-save below can store it with the plan.
    streamPlan(
      req,
      {
        onTrace: (step) => setTrace((prev) => [...prev, step]),
        onPlan: (resp) => {
          if (resp.error) setError(resp.error);
          setPlan(resp.plan ?? null);
          setWeather(resp.weather ?? null);
          setWeatherByCity(resp.weather_by_city ?? []);
          setWeatherByDay(resp.weather_by_day ?? []);
          setWarnings(resp.warnings ?? []);
          setQuestions(resp.clarifying_questions ?? []);
          setRunning(false);
          if (resp.plan) {
            setFormCollapsed(true);
            // Auto-save every generated trip to the user's history, so nothing
            // is lost even if they don't hit "Save". The manual Save bar then
            // updates this same entry (title/notes) rather than duplicating it.
            const p = resp.plan;
            const dest = p.route?.destination || p.city || req.city;
            const d = p.days ?? req.days ?? 1;
            const autoTitle = `${dest} · ${d > 1 ? `${d}-day` : "day"} trip`;
            savePlan({ title: autoTitle, notes: "", plan: p, request: req })
              .then((saved) => {
                setActiveSavedId(saved.id);
                setSeedTitle(saved.title);
                refreshSaved();
              })
              .catch(() => {
                /* non-fatal — history just won't include this one */
              });
          }
          // The generation consumed one trip from the quota.
          refreshUsage();
        },
        onError: (msg) => {
          setError(msg);
          setRunning(false);
        },
      },
      controller.signal
    );
  }, [refreshSaved, refreshUsage]);

  // Ask-first: on submit, fetch clarifying questions, then show the panel.
  const handleSubmit = useCallback(
    (req: PlanRequest) => {
      setPendingReq(req);
      setError(null);
      setPlan(null);
      setAsking(true);
      setClarifyLoading(true);
      setClarifyQuestions([]);
      setFormCollapsed(true);
      fetchClarify(req).then((res) => {
        if (!res.questions || res.questions.length === 0) {
          // Nothing to ask — just plan.
          runPlan(req);
          return;
        }
        setClarifyQuestions(res.questions);
        setClarifySource(res.source);
        setClarifyLoading(false);
      });
    },
    [runPlan]
  );

  const handleClarifySubmit = useCallback(
    (clarifications: string) => {
      if (!pendingReq) return;
      runPlan({ ...pendingReq, clarifications: clarifications || null });
    },
    [pendingReq, runPlan]
  );

  const handleClarifySkip = useCallback(() => {
    if (pendingReq) runPlan(pendingReq);
  }, [pendingReq, runPlan]);

  const handleSave = useCallback(
    async (title: string, notes: string) => {
      if (!plan) return;
      setSaving(true);
      setSaveMsg(null);
      try {
        const result = activeSavedId
          ? await updateSavedPlan(activeSavedId, { title, notes })
          : await savePlan({ title, notes, plan, request: currentRequest });
        setActiveSavedId(result.id);
        setSeedTitle(result.title);
        setSeedNotes(result.notes);
        setSaveMsg(activeSavedId ? "Updated ✓" : "Saved to your library ✓");
        await refreshSaved();
      } catch (e) {
        setSaveMsg((e as Error).message || "Couldn't save.");
      } finally {
        setSaving(false);
      }
    },
    [plan, activeSavedId, currentRequest, refreshSaved]
  );

  const handleLoad = useCallback((saved: SavedPlan) => {
    abortRef.current?.abort();
    setRunning(false);
    setAsking(false);
    setTrace([]);
    setError(null);
    setWarnings([]);
    setQuestions([]);
    setPlan(saved.plan);
    setWeather(null);
    // A saved plan keeps its route (and thus day/city structure), but we don't
    // persist per-city weather — it's time-sensitive. Clear it on load.
    setWeatherByCity([]);
    setWeatherByDay([]);
    setCurrentRequest(saved.request ?? null);
    setActiveSavedId(saved.id);
    setSeedTitle(saved.title);
    setSeedNotes(saved.notes);
    setSaveMsg(null);
    setPlanNonce((n) => n + 1);
    setFormCollapsed(true);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, []);

  const handleDelete = useCallback(
    async (id: string) => {
      try {
        await deleteSavedPlan(id);
        if (id === activeSavedId) {
          setActiveSavedId(null);
          setSaveMsg(null);
        }
        await refreshSaved();
      } catch {
        /* ignore */
      }
    },
    [activeSavedId, refreshSaved]
  );

  const handleSetToken = useCallback((token: string) => {
    setUserToken(token);
    setToken(token);
    setActiveSavedId(null);
  }, []);

  // Start fresh: clear the current plan and return to the planning form.
  const handleNewTrip = useCallback(() => {
    abortRef.current?.abort();
    setRunning(false);
    setAsking(false);
    setPlan(null);
    setTrace([]);
    setWeather(null);
    setWeatherByCity([]);
    setWeatherByDay([]);
    setWarnings([]);
    setQuestions([]);
    setError(null);
    setCurrentRequest(null);
    setPendingReq(null);
    setClarifyQuestions([]);
    setActiveSavedId(null);
    setSeedTitle("");
    setSeedNotes("");
    setSaveMsg(null);
    setFormCollapsed(false);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, []);

  // A readable title + short description for the collapsed trip bar, from the plan.
  const tripTitle = (() => {
    const dest = plan?.route?.destination || plan?.city || currentRequest?.city || "";
    if (!dest) return undefined;
    const d = plan?.days ?? currentRequest?.days ?? 1;
    const length = d > 1 ? `${d}-day trip` : "day trip";
    const org = currentRequest?.origin;
    return `${length} to ${dest}${org ? ` from ${org}` : ""}`;
  })();
  const tripSubtitle = (() => {
    const r = plan?.route;
    if (r && r.is_country && r.stops.length > 1) {
      return r.stops
        .slice()
        .sort((a, b) => a.order - b.order)
        .map((s) => s.city)
        .join(" → ");
    }
    if (plan?.summary) return plan.summary.length > 100 ? plan.summary.slice(0, 100) + "…" : plan.summary;
    return undefined;
  })();

  const formBar = (
    <PlannerForm
      onSubmit={handleSubmit}
      loading={running}
      collapsed={formCollapsed}
      onExpand={() => setFormCollapsed(false)}
      title={tripTitle}
      subtitle={tripSubtitle}
    />
  );
  const savedPanel = (
    <SavedPlans
      plans={savedPlans}
      activeId={activeSavedId}
      loading={savedLoading}
      userToken={userToken}
      onLoad={handleLoad}
      onDelete={handleDelete}
      onSetToken={handleSetToken}
    />
  );

  const showHero = !plan && !asking && !running;
  const hasWeather = weatherByCity.length > 0 || !!weather;

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand">
            <span className="logo">🗺️</span>
            <h1>Trip Planner</h1>
          </div>
          <div className="topbar-right">
            <span className="topbar-tag">Real itineraries · live costs · stays &amp; flights</span>
            {usage && !usage.unlimited && (
              <span
                className={`usage-chip ${usage.remaining === 0 ? "spent" : ""}`}
                title={`Trip generations left in the last ${usage.window_hours}h`}
              >
                🎫{" "}
                {usage.remaining === 0
                  ? "Limit reached"
                  : `${usage.remaining}/${usage.limit} trips left`}
              </span>
            )}
            {authEmail ? (
              <div className="auth-chip">
                <span className="auth-email" title={authEmail}>👤 {authEmail}</span>
                <button type="button" className="ghost sm" onClick={handleLogout}>
                  Log out
                </button>
              </div>
            ) : (
              <button type="button" className="primary sm" onClick={() => setShowAuth(true)}>
                Log in / Sign up
              </button>
            )}
          </div>
        </div>
      </header>

      {showAuth && (
        <AuthModal
          onClose={() => setShowAuth(false)}
          onAuthed={(email) => {
            setAuthEmail(email);
            setShowAuth(false);
          }}
        />
      )}

      <main className="main">
        {error && (
          <div className="card error-box banner">
            <strong>Something went wrong.</strong> {error}
          </div>
        )}

        {showHero && (
          <section className="hero2">
            <div className="hero2-copy">
              <h2>Where to next?</h2>
              <p>
                Tell me a destination and your vibe — I'll plan real, specific stops,
                estimate the full cost, and find places to stay. A few hours or a
                three-week trip.
              </p>
            </div>
            <div className="hero2-panel">{formBar}</div>
            <div className="hero2-saved">{savedPanel}</div>
          </section>
        )}

        {asking && (
          <div className="center-col">
            {formBar}
            <ClarifyPanel
              questions={clarifyQuestions}
              loading={clarifyLoading}
              source={clarifySource}
              onSubmit={handleClarifySubmit}
              onSkip={handleClarifySkip}
            />
          </div>
        )}

        {running && !plan && !asking && (
          <div className="center-col">
            {formBar}
            <TracePanel trace={trace} running={running} />
          </div>
        )}

        {plan && !asking && (
          <>
            {formBar}
            <div className="results">
              <div className="results-main">
                <div className="results-actions">
                  <button
                    type="button"
                    className="primary new-trip"
                    onClick={handleNewTrip}
                  >
                    ➕ Plan a new trip
                  </button>
                  <span className="results-actions-hint">
                    This trip is saved to your history below.
                  </span>
                </div>
                {saveMsg && <div className="save-msg">{saveMsg}</div>}
                <Clarify questions={questions} />
                <PlanView plan={plan} warnings={warnings} weatherByDay={weatherByDay} origin={currentRequest?.origin ?? undefined} />
                {plan.hotels && plan.hotels.length > 0 && (
                  <Hotels hotels={plan.hotels} city={plan.city} />
                )}
                <BookingLinks plan={plan} defaultOrigin={currentRequest?.origin ?? undefined} />
              </div>
              <aside className="results-side">
                <SavePlanBar
                  key={planNonce}
                  city={plan.city}
                  savedId={activeSavedId}
                  initialTitle={seedTitle}
                  initialNotes={seedNotes}
                  saving={saving}
                  onSave={handleSave}
                />
                {hasWeather && (
                  <WeatherBar weather={weather} weatherByCity={weatherByCity} />
                )}
                <TracePanel trace={trace} running={running} />
                {savedPanel}
              </aside>
            </div>
          </>
        )}
      </main>

      <footer>
        Data © OpenStreetMap contributors · Booking links open the provider's own
        site · Cost &amp; time figures are estimates.
      </footer>
    </div>
  );
}