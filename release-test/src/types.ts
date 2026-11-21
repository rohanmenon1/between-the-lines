export interface Position { line: number; character: number }
export interface CommentRange { start: Position; end: Position }
export interface Intent { prompt: string; source: "manual" | "codex" | "copilot"; sessionId?: string; timestamp?: string; eventId?: string; turnId?: string }
export interface OwnedComment { symbolId: string; commentText: string; codeHash: string }
export interface Metrics { inputTokens: number; outputTokens: number; modelCalls: number; elapsedMs: number }
export interface Suggestion {
  id: string; symbolId: string; symbolName: string; range: CommentRange;
  oldText: string; newText: string; commentText: string; codeHash: string;
  explanation: string; evidence: string[]; promptEvidence?: PromptEvidence[]; generation?: GenerationRecord;
}
export interface AnalysisResult {
  uri: string; sourceHash: string; suggestions: Suggestion[];
  skipped: { symbolId: string; reason: string }[]; metrics: Metrics;
}
export interface AnalyzeParams {
  uri: string; language: "python" | "java"; source: string; previousSource?: string;
  intent?: Intent; workspaceRoot?: string; contextFiles?: string[];
  ownedComments?: OwnedComment[]; maxSuggestions?: number; force?: boolean;
  knownRequirements?: RequirementRecord[];
}
export interface WorkerConfiguration { provider?: "local" | "custom"; endpoint?: string; model?: string; apiKey?: string; modelPath: string; threads: number; contextTokens: number; maxOutputTokens: number; idleSeconds: number }
export interface WorkerStatus { provider?: "local" | "custom"; protocolVersion: number; modelLoaded: boolean; modelPath?: string; backend?: string }

export interface PromptEvidence { quote: string; start: number; end: number; promptHash?: string }
export interface GenerationRecord { provider: "local" | "custom"; model: string; verified?: boolean }
export interface RequirementRecord {
  id?: string; path?: string; workspaceRoot?: string; source?: "codex" | "copilot";
  eventId?: string; promptEventId?: string; sessionId?: string; turnId?: string;
  symbolId: string; symbolName: string; quote: string; commentText: string; codeHash: string;
  prompt?: string; promptHash: string; quoteStart?: number; quoteEnd?: number;
  sourceHash?: string; observedBeforeHash?: string; supersedes?: string[];
  status?: string; scopeStatus?: string;
  commentKind?: "synthesized" | "quoted"; evidence?: PromptEvidence[]; generation?: GenerationRecord;
}
export type ObservedAnalyzeParams = AnalyzeParams;
export interface AutomaticAnalysisResult extends AnalysisResult {
  annotatedSource: string; annotatedSourceHash: string; requirements: RequirementRecord[];
}
