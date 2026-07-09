"""
Script AST parser for theatrical podcast generation.

Converts a theatrical script into a structured AST where acts, scenes,
sound cues, stage directions, and dialogue are separate node types. Only
dialogue/narration nodes are sent to TTS. All other nodes are preserved
in transcripts and JSON output for fidelity.

Architecture per ChatGPT Pro: parse once into a production IR, then
render multiple outputs (TTS manifest, rich transcript, mix plan, debug).
"""

import re
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any
from enum import Enum


class NodeType(Enum):
    """All possible node types in a theatrical script."""
    ACT = "act"
    SCENE = "scene"
    DIALOGUE = "dialogue"
    NARRATION = "narration"
    SOUND = "sound"
    MUSIC = "music"
    PAUSE = "pause"
    SILENCE = "silence"
    STAGE_DIRECTION = "stage_direction"
    BEAT = "beat"  # [beat] or (beat)
    BLANK = "blank"
    METADATA = "metadata"
    FRONTMATTER_BOUNDARY = "frontmatter_boundary"


@dataclass
class Node:
    """A single node in the production AST."""
    type: NodeType
    spoken: bool  # Should be rendered to TTS
    text: Optional[str] = None
    speaker: Optional[str] = None  # Only for dialogue/narration
    canonical_speaker: Optional[str] = None  # Normalized (HAL, ADA, etc.)
    voice_id: Optional[str] = None  # TTS voice assignment
    title: Optional[str] = None  # For act/scene/section headers
    cue_text: Optional[str] = None  # For sound/music cues
    duration_ms: Optional[int] = None  # For pauses/silence
    asset_key: Optional[str] = None  # Asset ID for sound/music
    render_audio: bool = False  # Schedule for audio rendering
    preserve_in_transcript: bool = True  # Include in final transcript
    style_context: Optional[Dict[str, str]] = None  # Delivery context
    line_no: int = 0  # Source line number for debugging

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary, excluding None fields and internal state."""
        return {
            k: v for k, v in asdict(self).items()
            if v is not None and not k.startswith('_')
        }


class ScriptParser:
    """Parse theatrical scripts into a structured AST."""

    # Regex patterns for line classification
    HEADING_RE = re.compile(r"^\s*(?P<hashes>#{1,6})\s+(?P<title>.+?)\s*$")
    CUE_RE = re.compile(
        r"^\s*(?:\*\*)?\[(?P<kind>SOUND|SFX|MUSIC|SEGMENT MUSIC|AMBIENCE|"
        r"PAUSE|SILENCE|BEAT)(?::|\s+)?(?P<body>[^\]]*)\](?:\*\*)?\s*$",
        re.IGNORECASE
    )
    SPEAKER_RE = re.compile(r"^\s*(?P<speaker>[A-Za-z][A-Za-z .'\-]{0,60}):\s*(?P<body>.*)$")
    THEMATIC_BREAK_RE = re.compile(r"^\s*-{3,}\s*$")
    BOLD_META_RE = re.compile(r"^\*\*(.+?):\*\*\s*(.*)$")
    STAGE_DIRECTION_RE = re.compile(r"^\s*\((.+?)\)\s*$")
    BLOCKQUOTE_CONTINUATION_RE = re.compile(r"^\s*>\s*(.+)$")

    def __init__(self, speaker_map: Optional[Dict[str, str]] = None):
        """Initialize parser with speaker normalization map.

        Args:
            speaker_map: dict mapping speaker names to normalized forms (e.g. "Hal Turing" -> "HAL")
        """
        self.speaker_map = speaker_map or self._default_speaker_map()
        self.nodes: List[Node] = []
        self.current_speaker: Optional[str] = None
        self.current_speaker_canonical: Optional[str] = None
        self.current_text: List[str] = []
        self.current_act: Optional[str] = None
        self.current_scene: Optional[str] = None

    @staticmethod
    def _default_speaker_map() -> Dict[str, str]:
        """Default speaker normalization (case-insensitive variants)."""
        base = {
            "Hal Turing": "HAL",
            "Hal": "HAL",
            "Dr. Ada Shannon": "ADA",
            "Ada Shannon": "ADA",
            "Ada": "ADA",
        }
        # Add lowercase variants
        return {**base, **{k.lower(): v for k, v in base.items()}}

    def parse(self, text: str) -> List[Node]:
        """Parse script text into AST.

        Args:
            text: raw script content

        Returns:
            List of Node objects in document order
        """
        self.nodes = []
        lines = text.split('\n')

        # Skip YAML frontmatter if present
        start_idx = 0
        if lines and lines[0].strip() == '---':
            # Find closing ---
            for i in range(1, len(lines)):
                if lines[i].strip() == '---':
                    start_idx = i + 1
                    break

        for line_no, line in enumerate(lines[start_idx:], start_idx + 1):
            self._process_line(line, line_no)

        # Flush final dialogue segment
        self._flush_dialogue()

        return self.nodes

    def _process_line(self, line: str, line_no: int):
        """Classify and process a single line."""
        stripped = line.strip()

        # Blank line
        if not stripped:
            self._flush_dialogue()
            self.nodes.append(Node(
                type=NodeType.BLANK,
                spoken=False,
                preserve_in_transcript=False,
                line_no=line_no
            ))
            return

        # Frontmatter boundary
        if self.THEMATIC_BREAK_RE.match(stripped):
            self._flush_dialogue()
            self.nodes.append(Node(
                type=NodeType.FRONTMATTER_BOUNDARY,
                spoken=False,
                preserve_in_transcript=False,
                line_no=line_no
            ))
            return

        # Heading (act, scene, section)
        if m := self.HEADING_RE.match(stripped):
            self._flush_dialogue()
            title = m.group("title")
            hashes = m.group("hashes")
            level = len(hashes)

            if "ACT" in title.upper():
                self.current_act = title
                self.current_scene = None
                self.nodes.append(Node(
                    type=NodeType.ACT,
                    spoken=False,
                    title=title,
                    preserve_in_transcript=True,
                    line_no=line_no
                ))
            elif "SCENE" in title.upper():
                self.current_scene = title
                self.nodes.append(Node(
                    type=NodeType.SCENE,
                    spoken=False,
                    title=title,
                    preserve_in_transcript=True,
                    line_no=line_no
                ))
            else:
                # Generic section heading
                self.nodes.append(Node(
                    type=NodeType.STAGE_DIRECTION,
                    spoken=False,
                    text=f"[Section: {title}]",
                    preserve_in_transcript=True,
                    line_no=line_no
                ))
            return

        # Sound/Music/Pause cue
        if m := self.CUE_RE.match(stripped):
            self._flush_dialogue()
            kind = m.group("kind").upper()
            cue_text = m.group("body").strip()

            if kind == "PAUSE" or kind == "BEAT":
                self.nodes.append(Node(
                    type=NodeType.PAUSE,
                    spoken=False,
                    duration_ms=500,  # Default beat/pause
                    render_audio=True,
                    line_no=line_no
                ))
            elif kind == "SILENCE":
                self.nodes.append(Node(
                    type=NodeType.SILENCE,
                    spoken=False,
                    duration_ms=1000,
                    render_audio=True,
                    line_no=line_no
                ))
            elif kind in ("MUSIC", "SEGMENT MUSIC", "AMBIENCE"):
                self.nodes.append(Node(
                    type=NodeType.MUSIC,
                    spoken=False,
                    cue_text=cue_text,
                    render_audio=True,
                    line_no=line_no
                ))
            else:  # SOUND, SFX
                self.nodes.append(Node(
                    type=NodeType.SOUND,
                    spoken=False,
                    cue_text=cue_text,
                    render_audio=True,
                    line_no=line_no
                ))
            return

        # Speaker dialogue (standard form: Name: text)
        if m := self.SPEAKER_RE.match(stripped):
            speaker_name = m.group("speaker").strip()
            text = m.group("body").strip()

            if canonical := self.speaker_map.get(speaker_name):
                # Flush previous speaker's dialogue
                self._flush_dialogue()

                self.current_speaker = speaker_name
                self.current_speaker_canonical = canonical
                if text:
                    self.current_text.append(text)
                return

        # Blockquote continuation (> text)
        if m := self.BLOCKQUOTE_CONTINUATION_RE.match(stripped):
            text = m.group(1).strip()
            if self.current_speaker and text:
                self.current_text.append(text)
                return

        # Stage direction in parentheses
        if m := self.STAGE_DIRECTION_RE.match(stripped):
            direction = m.group(1).strip()
            if self.current_speaker:
                # Append to current speaker's context (don't speak)
                self.current_text.append(f"[{direction}]")
            else:
                # Standalone stage direction
                self._flush_dialogue()
                self.nodes.append(Node(
                    type=NodeType.STAGE_DIRECTION,
                    spoken=False,
                    text=direction,
                    preserve_in_transcript=True,
                    line_no=line_no
                ))
            return

        # Metadata in bold (**Key: Value**)
        if m := self.BOLD_META_RE.match(stripped):
            self._flush_dialogue()
            key = m.group(1)
            value = m.group(2)
            self.nodes.append(Node(
                type=NodeType.METADATA,
                spoken=False,
                text=f"{key}: {value}",
                preserve_in_transcript=False,
                line_no=line_no
            ))
            return

        # Non-speaker text (might be narration or continuation)
        if stripped and not self.current_speaker:
            # Treat as narration (speaker A)
            self._flush_dialogue()
            self.current_speaker = "Narrator"
            self.current_speaker_canonical = "A"
            self.current_text = [stripped]
            return
        elif self.current_speaker and stripped:
            # Continuation of current speaker
            self.current_text.append(stripped)
            return

    def _flush_dialogue(self):
        """Convert accumulated dialogue text into dialogue node(s)."""
        if not self.current_text or not self.current_speaker:
            self.current_speaker = None
            self.current_speaker_canonical = None
            self.current_text = []
            return

        # Join lines and clean internal stage directions
        full_text = '\n'.join(self.current_text).strip()

        # Split on stage directions in brackets [like this]
        # but keep the brackets for semantic marking
        segments = self._split_on_stage_directions(full_text)

        for segment_text in segments:
            if segment_text.strip():
                cleaned = self._clean_dialogue_text(segment_text)
                if cleaned:
                    self.nodes.append(Node(
                        type=NodeType.DIALOGUE,
                        spoken=True,
                        text=cleaned,
                        speaker=self.current_speaker,
                        canonical_speaker=self.current_speaker_canonical,
                        preserve_in_transcript=True
                    ))

        self.current_speaker = None
        self.current_speaker_canonical = None
        self.current_text = []

    @staticmethod
    def _split_on_stage_directions(text: str) -> List[str]:
        """Split dialogue on inline stage directions [like this]."""
        # For now, keep it simple: return as-is
        # In future, could split and emit stage_direction nodes
        return [text]

    @staticmethod
    def _clean_dialogue_text(text: str) -> str:
        """Remove inline metadata/stage directions from dialogue only.

        Keeps the core dialogue but removes:
        - Inline [SOUND: ...] markers
        - Inline [MUSIC] markers
        - Bold stage directions **[like this]**
        - Blockquote markers >
        """
        # Remove **[stage directions]**
        text = re.sub(r'\*\*\[([^\]]*)\]\*\*', '', text)

        # Remove [stage directions]
        text = re.sub(r'\[([A-Z]+[^\]]*)\]', '', text)

        # Remove leading blockquote markers
        text = re.sub(r'^\s*>\s+', '', text, flags=re.MULTILINE)

        # Clean up excess whitespace
        text = re.sub(r'\s+', ' ', text).strip()

        return text


def render_tts_manifest(nodes: List[Node]) -> List[Dict[str, Any]]:
    """Extract only dialogue/narration nodes for TTS."""
    manifest = []
    current_act = None
    current_scene = None

    for node in nodes:
        if node.type == NodeType.ACT:
            current_act = node.title
        elif node.type == NodeType.SCENE:
            current_scene = node.title
        elif node.spoken and node.text:
            manifest.append({
                "speaker": node.canonical_speaker or "A",
                "text": node.text,
                "style_context": {
                    "act": current_act,
                    "scene": current_scene,
                    "delivery": "theatrical"
                }
            })

    return manifest


def render_rich_transcript(nodes: List[Node]) -> str:
    """Generate a rich transcript including all preserved nodes."""
    lines = []

    for node in nodes:
        if not node.preserve_in_transcript:
            continue

        if node.type == NodeType.ACT:
            lines.append(f"## {node.title}")
            lines.append("")
        elif node.type == NodeType.SCENE:
            lines.append(f"### {node.title}")
            lines.append("")
        elif node.type == NodeType.DIALOGUE:
            lines.append(f"**{node.speaker}:** {node.text}")
            lines.append("")
        elif node.type == NodeType.NARRATION:
            lines.append(f"*{node.text}*")
            lines.append("")
        elif node.type == NodeType.SOUND:
            lines.append(f"[SOUND: {node.cue_text}]")
            lines.append("")
        elif node.type == NodeType.MUSIC:
            lines.append(f"[MUSIC: {node.cue_text}]")
            lines.append("")
        elif node.type == NodeType.STAGE_DIRECTION:
            lines.append(f"({node.text})")
            lines.append("")
        elif node.type == NodeType.PAUSE:
            lines.append("[beat]")
            lines.append("")

    return '\n'.join(lines)


def render_mix_plan(nodes: List[Node]) -> List[Dict[str, Any]]:
    """Generate audio mixing plan with TTS clips, sounds, music, pauses."""
    plan = []
    current_speaker = None

    for node in nodes:
        if node.type == NodeType.DIALOGUE and node.text:
            plan.append({
                "type": "dialogue",
                "speaker": node.canonical_speaker or "A",
                "text": node.text
            })
        elif node.type == NodeType.SOUND:
            plan.append({
                "type": "sound",
                "cue": node.cue_text,
                "render": True
            })
        elif node.type == NodeType.MUSIC:
            plan.append({
                "type": "music",
                "cue": node.cue_text,
                "render": True
            })
        elif node.type == NodeType.PAUSE:
            plan.append({
                "type": "pause",
                "duration_ms": node.duration_ms or 500
            })

    return plan


# Audit/validation helper
@dataclass
class ParseAudit:
    """Statistics and validation report from parsing."""
    raw_nonblank_lines: int = 0
    nodes_total: int = 0
    dialogue_nodes: int = 0
    act_nodes: int = 0
    scene_nodes: int = 0
    sound_nodes: int = 0
    music_nodes: int = 0
    stage_direction_nodes: int = 0
    spoken_words: int = 0
    estimated_duration_seconds: float = 0.0
    dropped_nonblank_lines: int = 0
    unresolved_cues: int = 0


def audit_parse(nodes: List[Node], raw_text: str) -> ParseAudit:
    """Audit parsing completeness and quality."""
    raw_lines = [l for l in raw_text.split('\n') if l.strip()]
    audit = ParseAudit(
        raw_nonblank_lines=len(raw_lines),
        nodes_total=len(nodes),
        dialogue_nodes=sum(1 for n in nodes if n.type == NodeType.DIALOGUE),
        act_nodes=sum(1 for n in nodes if n.type == NodeType.ACT),
        scene_nodes=sum(1 for n in nodes if n.type == NodeType.SCENE),
        sound_nodes=sum(1 for n in nodes if n.type == NodeType.SOUND),
        music_nodes=sum(1 for n in nodes if n.type == NodeType.MUSIC),
        stage_direction_nodes=sum(1 for n in nodes if n.type == NodeType.STAGE_DIRECTION),
    )

    # Count spoken words
    spoken_text = ' '.join(
        n.text for n in nodes
        if n.spoken and n.text
    )
    audit.spoken_words = len(spoken_text.split())

    # Estimate duration: ~150 words per minute
    audit.estimated_duration_seconds = (audit.spoken_words / 150.0) * 60

    return audit
