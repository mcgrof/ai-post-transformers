#!/usr/bin/env python3
"""
Transcript Analysis Template for VERA Patch Notes

Purpose: Analyze episode transcripts to detect loops, rituals, callbacks,
and other patterns for editorial review.

Usage: python transcript_analysis_template.py episode_N_transcript.txt

Output: analysis_report_episode_N.md (ready for Luis to review)

IMPORTANT NOTES:
- This is a LINT PASS, not the source of truth
- Automation surfaces candidates; humans make decisions
- Use this to seed editorial decisions, not replace judgment
- Some patterns require manual review (tone, context, mutation quality)
"""

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Tuple

# ============================================================================
# Data Structures
# ============================================================================

@dataclass
class PhraseOccurrence:
    """Track when and where a phrase appears"""
    phrase: str
    speaker: str  # "hal", "ada", or "vera"
    timestamp: str  # rough position in transcript
    context: str  # surrounding sentence
    is_exact_match: bool  # True if exact, False if fuzzy match


@dataclass
class PatternAnalysis:
    """Results of analyzing one host's patterns"""
    host_name: str
    phrases_detected: List[PhraseOccurrence]
    phrase_frequency: Dict[str, int]
    stale_risk_phrases: List[Tuple[str, int]]  # (phrase, count)
    new_phrases: List[str]
    callbacks_detected: List[str]
    host_asymmetries: Dict[str, str]  # specific Hal/Ada patterns


@dataclass
class AnalysisReport:
    """Complete analysis output for one episode"""
    episode_number: int
    episode_title: str
    analysis_date: str
    hal_analysis: PatternAnalysis
    ada_analysis: PatternAnalysis
    callbacks: List[str]
    listener_signals: Dict[str, str]  # subjective audience reactions if tracked
    editorial_recommendations: List[str]


# ============================================================================
# Known Stale Phrases (Update as SOUL.md Changes)
# ============================================================================

STALE_PHRASES = {
    # Hal's deprecated/monitored phrases
    "why did this capture my attention": {
        "host": "hal",
        "status": "deprecated",
        "alternative": "stakes-first opening formula",
        "reason": "overused 47+ times, semantic saturation"
    },
    "this is just (.+?) with extra steps": {
        "host": "hal",
        "status": "monitored",
        "frequency_threshold": 3,
        "reason": "loop tendency, at saturation"
    },
    "the benchmark is (.+?)": {
        "host": "hal",
        "status": "monitored",
        "frequency_threshold": 3,
        "reason": "accurate but nearing predictability"
    },
    "i don't hate this": {
        "host": "hal",
        "status": "monitored",
        "frequency_threshold": 4,
        "reason": "false civility, overuse pattern"
    },
    # Ada's deprecated/monitored phrases
    "to be fair": {
        "host": "ada",
        "status": "monitored",
        "frequency_threshold": 3,
        "reason": "trending high, becoming filler"
    },
    "the broader context is": {
        "host": "ada",
        "status": "monitored",
        "frequency_threshold": 2,
        "reason": "saturation concern, context before stakes"
    },
    "we should be careful": {
        "host": "ada",
        "status": "monitored",
        "frequency_threshold": 2,
        "reason": "hedging pattern, pre-emptive appeasement"
    }
}

# Growth edges we're seeding (look for evidence of these)
GROWTH_EDGES = {
    "hal": [
        "curiosity before prosecution",
        "one sincere compliment",
        "permit vulnerability",
        "vary threat models"
    ],
    "ada": [
        "lead with consequence before context",
        "let joke breathe after landing",
        "sharper first verdicts",
        "compress opening context"
    ]
}

# Callbacks to track (from prior notable episodes)
PRIOR_CALLBACKS = [
    "Kokoro episode",
    "Cognizant: New Work New World",
    "We're Open Source episode",
    "memory systems",
    "benchmark hostage situation"
]


# ============================================================================
# Analysis Functions
# ============================================================================

class TranscriptAnalyzer:
    """Main analysis engine"""

    def __init__(self, transcript_text: str, episode_num: int, episode_title: str):
        self.transcript = transcript_text
        self.episode_num = episode_num
        self.episode_title = episode_title
        self.lines = transcript_text.split('\n')

    def parse_lines(self) -> Dict[str, List[str]]:
        """
        Parse transcript into speaker segments.
        Format expected: "HAL: text here" or "ADA: text here"
        """
        segments = {"hal": [], "ada": [], "vera": [], "other": []}

        for line in self.lines:
            line = line.strip()
            if not line:
                continue

            # Extract speaker and content
            if line.startswith("HAL:"):
                segments["hal"].append(line[4:].strip())
            elif line.startswith("ADA:"):
                segments["ada"].append(line[4:].strip())
            elif line.startswith("VERA:"):
                segments["vera"].append(line[5:].strip())
            else:
                segments["other"].append(line)

        return segments

    def detect_stale_phrases(self, segments: Dict[str, List[str]]) -> PatternAnalysis:
        """
        Scan for deprecated/monitored phrases.
        Returns PatternAnalysis for each host.
        """

        results = {
            "hal": PatternAnalysis("hal", [], {}, [], [], [], {}),
            "ada": PatternAnalysis("ada", [], {}, [], [], [], {})
        }

        for host in ["hal", "ada"]:
            host_lines = segments[host]
            phrase_count = defaultdict(int)
            occurrences = []

            for line_idx, line in enumerate(host_lines):
                line_lower = line.lower()

                # Check exact matches first
                for phrase, config in STALE_PHRASES.items():
                    if config["host"] != host:
                        continue

                    # Try exact match
                    if phrase in line_lower:
                        phrase_count[phrase] += 1
                        occurrences.append(
                            PhraseOccurrence(
                                phrase=phrase,
                                speaker=host,
                                timestamp=f"line_{line_idx}",
                                context=line[:100],  # first 100 chars
                                is_exact_match=True
                            )
                        )

                    # Try regex match for pattern phrases
                    if "(.+?)" in phrase:
                        pattern = re.compile(phrase, re.IGNORECASE)
                        if pattern.search(line_lower):
                            phrase_count[phrase] += 1
                            match = pattern.search(line_lower)
                            if match:
                                occurrences.append(
                                    PhraseOccurrence(
                                        phrase=phrase,
                                        speaker=host,
                                        timestamp=f"line_{line_idx}",
                                        context=line[:100],
                                        is_exact_match=False
                                    )
                                )

            # Identify which are at risk
            stale_risk = []
            for phrase, count in phrase_count.items():
                config = STALE_PHRASES.get(phrase, {})
                if config.get("status") == "monitored":
                    threshold = config.get("frequency_threshold", 2)
                    if count >= threshold:
                        stale_risk.append((phrase, count))
                elif config.get("status") == "deprecated":
                    if count > 0:
                        stale_risk.append((phrase, count))  # Any use is concerning

            results[host].phrases_detected = occurrences
            results[host].phrase_frequency = dict(phrase_count)
            results[host].stale_risk_phrases = stale_risk

        return results

    def detect_growth_edges(self, segments: Dict[str, List[str]]) -> Dict[str, List[str]]:
        """
        Look for evidence of growth edges being deployed.
        Returns dict of what we found.
        """
        findings = {"hal": [], "ada": []}

        # Hal growth edges
        hal_lines = ' '.join(segments["hal"])
        if "curious" in hal_lines.lower() or "question" in hal_lines.lower():
            # Manual: requires checking if questions came before contempt
            findings["hal"].append("curiosity_before_prosecution: POSSIBLE")

        if "actually good" in hal_lines.lower() or "impressive" in hal_lines.lower():
            findings["hal"].append("sincere_compliment: POSSIBLE")

        # Ada growth edges
        ada_lines = ' '.join(segments["ada"])
        if ada_lines.lower().count("to be fair") < 3:
            findings["ada"].append("reduced_hedging: YES")

        if "here's why" in ada_lines.lower():
            findings["ada"].append("stakes_first: POSSIBLE")

        return findings

    def detect_callbacks(self, segments: Dict[str, List[str]]) -> List[str]:
        """
        Look for callbacks to prior episodes or running gags.
        """
        callbacks = []
        all_text = ' '.join(
            segments["hal"] + segments["ada"] + segments["vera"]
        ).lower()

        for callback_term in PRIOR_CALLBACKS:
            if callback_term.lower() in all_text:
                callbacks.append(callback_term)

        return callbacks

    def assess_listener_signals(self) -> Dict[str, str]:
        """
        This is subjective—requires manual input or external data.
        For now, template shows what to track.
        """
        signals = {
            "Laughter detected": "Monitor for comedic beats that landed",
            "Applause/reaction": "Watch for moments where show worked",
            "Audience engagement": "Did listeners stay or skip?",
            "Twitter reactions": "Post episode, collect feedback",
            "Stale phrase appearance": "Did old patterns surface?",
            "New phrase deployment": "Did hosts try new vocabulary?"
        }
        return signals

    def generate_report(self) -> AnalysisReport:
        """
        Run complete analysis and generate report.
        """
        segments = self.parse_lines()
        stale_results = self.detect_stale_phrases(segments)
        growth_edges = self.detect_growth_edges(segments)
        callbacks = self.detect_callbacks(segments)
        listener_signals = self.assess_listener_signals()

        # Build editorial recommendations
        recommendations = []

        # Check for deprecations
        for host in ["hal", "ada"]:
            analysis = stale_results[host]
            for phrase, count in analysis.stale_risk_phrases:
                config = STALE_PHRASES.get(phrase, {})
                if config.get("status") == "deprecated":
                    recommendations.append(
                        f"CONSIDER: Deprecate '{phrase}' (used {count} times, status=deprecated)"
                    )
                elif config.get("status") == "monitored" and count >= config.get("frequency_threshold", 2):
                    recommendations.append(
                        f"MONITOR: '{phrase}' used {count} times (threshold={config.get('frequency_threshold')})"
                    )

        # Check for growth edges
        if any("POSSIBLE" in str(v) or "YES" in str(v) for v in growth_edges.values()):
            recommendations.append("Growth edges detected—requires manual verification")

        if callbacks:
            recommendations.append(f"Callbacks deployed: {', '.join(callbacks)}")

        if not recommendations:
            recommendations.append("No significant patterns detected this episode")

        return AnalysisReport(
            episode_number=self.episode_num,
            episode_title=self.episode_title,
            analysis_date=datetime.now().isoformat(),
            hal_analysis=stale_results["hal"],
            ada_analysis=stale_results["ada"],
            callbacks=callbacks,
            listener_signals=listener_signals,
            editorial_recommendations=recommendations
        )

    def format_markdown_report(self, report: AnalysisReport) -> str:
        """
        Format analysis report as markdown for Luis to review.
        """
        md = []
        md.append(f"# Episode {report.episode_number} Analysis")
        md.append(f"**Title:** {report.episode_title}")
        md.append(f"**Analysis Date:** {report.analysis_date}")
        md.append("")

        # Hal patterns
        md.append("## Hal's Patterns")
        if report.hal_analysis.stale_risk_phrases:
            md.append("### At-Risk Phrases")
            for phrase, count in report.hal_analysis.stale_risk_phrases:
                config = STALE_PHRASES.get(phrase, {})
                status = config.get("status", "unknown")
                md.append(f"- `{phrase}` — used {count}x, status={status}")
        else:
            md.append("No stale phrase risks detected.")

        # Ada patterns
        md.append("")
        md.append("## Ada's Patterns")
        if report.ada_analysis.stale_risk_phrases:
            md.append("### At-Risk Phrases")
            for phrase, count in report.ada_analysis.stale_risk_phrases:
                config = STALE_PHRASES.get(phrase, {})
                status = config.get("status", "unknown")
                md.append(f"- `{phrase}` — used {count}x, status={status}")
        else:
            md.append("No stale phrase risks detected.")

        # Callbacks
        if report.callbacks:
            md.append("")
            md.append("## Callbacks Detected")
            for callback in report.callbacks:
                md.append(f"- {callback}")

        # Listener signals
        md.append("")
        md.append("## Listener Signals (Manual Input Required)")
        for signal, note in report.listener_signals.items():
            md.append(f"- **{signal}:** {note}")

        # Recommendations
        md.append("")
        md.append("## Editorial Recommendations")
        for rec in report.editorial_recommendations:
            md.append(f"- {rec}")

        md.append("")
        md.append("---")
        md.append("**Next Step:** Luis reviews and makes patch decisions.")

        return "\n".join(md)


# ============================================================================
# Main Entry Point
# ============================================================================

def main():
    """
    Example usage.

    In production:
    - Read transcript from file
    - Pass to TranscriptAnalyzer
    - Generate report
    - Save markdown for Luis to review
    """
    # Example transcript (in real use, read from file)
    example_transcript = """
    HAL: So why did this capture my attention? This paper is basically just X with extra steps.
    ADA: To be fair, that's not entirely wrong, but the broader context is worth understanding.
    HAL: The benchmark is doing a lot of work here.
    ADA: We should be careful about dismissing the methodology too quickly.
    """

    # Analyze
    analyzer = TranscriptAnalyzer(
        transcript_text=example_transcript,
        episode_num=47,
        episode_title="Example Episode"
    )

    report = analyzer.generate_report()
    markdown_report = analyzer.format_markdown_report(report)

    # Print for review
    print(markdown_report)

    # In production, save to file:
    # with open(f"analysis_report_episode_{report.episode_number}.md", "w") as f:
    #     f.write(markdown_report)
    # print(f"Report saved: analysis_report_episode_{report.episode_number}.md")


# ============================================================================
# Usage Notes
# ============================================================================

"""
HOW TO USE THIS SCRIPT:

1. After episode publishes, export transcript to .txt file
   Format expected: "SPEAKER: transcript text here"

2. Run analysis:
   python transcript_analysis_template.py episode_47_transcript.txt

3. Output: analysis_report_episode_47.md (ready for Luis)

4. Luis reads report and makes decisions:
   - DEPRECATE: Mark phrase for final burial
   - PROMOTE: Mark new phrase as approved vocabulary
   - MONITOR: Watch phrase frequency (flag if threshold exceeded)
   - GROWTH: Confirm if growth edge actually deployed

5. If decisions made: Update SOUL.md and commit to git

IMPORTANT CAVEATS:

This is a LINT PASS, not decision authority.

The script can detect:
✓ Exact phrase matches
✓ Regex pattern matches
✓ Phrase frequency trends
✓ Callback references
✗ Tone or context (requires human judgment)
✗ Whether repetition is a ritual or a loop (requires human decision)
✗ Quality of callback mutation (requires listening)

Always verify automation findings before acting on them.

Example false positives:
- "to be fair" in air (Ada's phrase) vs "to be fair" in guest quote
- "benchmark" in paper title vs Hal's "benchmark" complaint
- Callback that's quoted ironically vs genuinely referenced

Manual review is not optional—it's essential.
"""

if __name__ == "__main__":
    main()
