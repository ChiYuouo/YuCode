"""将 Team 成员 Worktree 分支安全收敛到 Lead 分支。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess

from yucode.teams.models import AgentTeam, MemberState


@dataclass(frozen=True)
class MemberMergeResult:
    member: str
    status: str
    detail: str


@dataclass(frozen=True)
class TeamMergeResult:
    success: bool
    members: tuple[MemberMergeResult, ...]

    @property
    def summary(self) -> str:
        return "\n".join(f"{item.member}：{item.status}（{item.detail}）" for item in self.members) or "没有可收敛的成员分支。"


class TeamMergeError(ValueError):
    pass


class TeamMergeService:
    def __init__(self, repository_root: Path, timeout_seconds: float = 30.0, scaffolding: tuple[str, ...] = ()) -> None:
        self._root = repository_root.resolve(); self._timeout = timeout_seconds; self._scaffolding = scaffolding

    def _exclusions(self) -> tuple[str, ...]:
        """成员 Worktree 内由初始化器创建的脚手架不计入改动。"""
        if not self._scaffolding:
            return ()
        return ("--", ".", *(f":(exclude){item}" for item in self._scaffolding))

    def merge(self, team: AgentTeam) -> TeamMergeResult:
        if self._git("status", "--porcelain"):
            raise TeamMergeError("Lead 工作目录存在未提交修改，不能开始团队收敛。")
        results: list[MemberMergeResult] = []
        for member in team.members:
            if member.worktree_slug is None:
                results.append(MemberMergeResult(member.name, "跳过", "只读成员没有分支")); continue
            if member.state is MemberState.RUNNING:
                results.append(MemberMergeResult(member.name, "跳过", "成员仍在运行")); continue
            branch = f"yucode/worktree/{member.worktree_slug}"
            # 分支可能已被用户显式丢弃；此时没有可收敛改动，不能当成冲突中止整轮收敛。
            if self._run("show-ref", "--verify", "--quiet", f"refs/heads/{branch}", allow_failure=True).returncode != 0:
                results.append(MemberMergeResult(member.name, "跳过", f"分支 {branch} 已不存在")); continue
            if self._git("merge-base", "--is-ancestor", branch, "HEAD", allow_failure=True).returncode == 0:
                results.append(MemberMergeResult(member.name, "已包含", branch)); continue
            completed = self._run("merge", "--no-ff", "--no-edit", branch, allow_failure=True)
            if completed.returncode == 0:
                results.append(MemberMergeResult(member.name, "已合并", branch)); continue
            conflicts = self._git("diff", "--name-only", "--diff-filter=U", allow_failure=True).stdout.strip()
            conflict_paths = tuple(item for item in conflicts.splitlines() if item)
            if conflict_paths and self._resolve_append_only(conflict_paths):
                results.append(MemberMergeResult(member.name, "已自动合并", "安全合并双方追加内容")); continue
            self._run("merge", "--abort", allow_failure=True)
            results.append(MemberMergeResult(member.name, "冲突", conflicts or completed.stderr.strip() or "无法自动解决"))
            return TeamMergeResult(False, tuple(results))
        return TeamMergeResult(True, tuple(results))

    def _resolve_append_only(self, paths: tuple[str, ...]) -> bool:
        resolved: list[tuple[Path, str]] = []
        for relative in paths:
            try:
                base = self._run("show", f":1:{relative}").stdout
                ours = self._run("show", f":2:{relative}").stdout
                theirs = self._run("show", f":3:{relative}").stdout
            except TeamMergeError:
                return False
            if not ours.startswith(base) or not theirs.startswith(base):
                return False
            first = ours[len(base):]; second = theirs[len(base):]
            merged = base + first + (second if second != first else "")
            target = (self._root / relative).resolve()
            try:
                target.relative_to(self._root)
            except ValueError:
                return False
            resolved.append((target, merged))
        for target, content in resolved:
            target.write_text(content, encoding="utf-8")
        self._run("add", "--", *(str(path.relative_to(self._root)) for path, _ in resolved))
        self._run("commit", "--no-edit")
        return True

    def commit_member(self, member_name: str, workspace_root: Path) -> str | None:
        """提交成员 Worktree 的全部已完成改动；初始化产生的脚手架不计入改动。"""
        status = self._run_at(workspace_root, "status", "--porcelain", *self._exclusions()).stdout.strip()
        if not status:
            return None
        self._run_at(workspace_root, "add", "-A", *self._exclusions())
        self._run_at(workspace_root, "commit", "-m", f"yucode(team): {member_name} 完成任务")
        return self._run_at(workspace_root, "rev-parse", "HEAD").stdout.strip()

    def safe_to_cleanup(self, workspace_root: Path, branch: str) -> tuple[bool, str]:
        """判断成员资源能否安全移除；目录已被外部删除时给出可操作的下一步。"""
        if not workspace_root.is_dir():
            if self._branch_merged(branch):
                return True, "成员 Worktree 目录已不存在，其改动已合并到 Lead"
            return False, (
                f"成员 Worktree 目录已被外部删除，但分支 {branch} 尚未合并到 Lead；"
                f"请先让 Lead 合并该分支，或明确丢弃分支 {branch} 后重试"
            )
        if self._run_at(workspace_root, "status", "--porcelain", *self._exclusions()).stdout.strip():
            return False, "Worktree 仍有未提交修改"
        if not self._branch_merged(branch):
            return False, f"分支 {branch} 尚未合并到 Lead"
        return True, "成员改动已合并且工作目录干净"

    def _branch_merged(self, branch: str) -> bool:
        return self._run("merge-base", "--is-ancestor", branch, "HEAD", allow_failure=True).returncode == 0

    def resource_is_absent(self, workspace_root: Path, branch: str) -> bool:
        """仅当 Worktree 目录和本地分支都不存在时，确认资源已由外部清理。"""
        if workspace_root.exists():
            return False
        result = self._run("show-ref", "--verify", "--quiet", f"refs/heads/{branch}", allow_failure=True)
        return result.returncode != 0

    def _git(self, *args: str, allow_failure: bool = False):
        result = self._run(*args, allow_failure=allow_failure)
        return result if allow_failure else result.stdout.strip()

    def _run(self, *args: str, allow_failure: bool = False) -> subprocess.CompletedProcess[str]:
        return self._run_at(self._root, *args, allow_failure=allow_failure)

    def _run_at(self, root: Path, *args: str, allow_failure: bool = False) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(("git", *args), cwd=root, capture_output=True, text=True,
                                    encoding="utf-8", check=False, timeout=self._timeout)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise TeamMergeError(f"无法执行 Git 收敛：{error}") from error
        if result.returncode != 0 and not allow_failure:
            raise TeamMergeError((result.stderr or result.stdout).strip() or "Git 收敛失败。")
        return result
