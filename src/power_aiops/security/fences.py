import re
from dataclasses import dataclass

# Doc 4.1.3: blacklist high-risk shell/SQL patterns (extend as needed)
_DEFAULT_PATTERNS: tuple[re.Pattern[str], ...] = (
    # === 文件系统危险操作 ===
    re.compile(r"\brm\s+-[rfv]+\b", re.IGNORECASE),  # rm -rf, rm -rfv, etc.
    re.compile(r"\brm\s+-rf\s+/", re.IGNORECASE),  # rm -rf / (root deletion)
    re.compile(r"\brm\s+-\s*\w+", re.IGNORECASE),  # rm -<flag> pattern
    re.compile(r":\s*!\s*rm\b", re.IGNORECASE),  # :!rm shell escape
    re.compile(r"\bdd\s+if\b", re.IGNORECASE),  # dd if=<input file>
    re.compile(r"\bmkfifo\b", re.IGNORECASE),  # named pipe creation
    re.compile(r"\bmknod\b", re.IGNORECASE),  # device file creation
    re.compile(r"\bln\s+-sf\b", re.IGNORECASE),  # symlink overwrite
    re.compile(r"\bchmod\s+-R?\s+777\b", re.IGNORECASE),  # world-writable permissions

    # === 数据库危险操作 ===
    re.compile(r"\bdrop\s+(table|database|index|view|procedure|function)\b", re.IGNORECASE),
    re.compile(r"\btruncate\b\s+\w+", re.IGNORECASE),
    re.compile(r"\bdelete\s+from\b\s+\w+\s*;?\s*$", re.IGNORECASE | re.MULTILINE),  # delete all rows
    re.compile(r"\bdrop\s+user\b", re.IGNORECASE),
    re.compile(r"\bshutdown\b", re.IGNORECASE),  # database shutdown
    re.compile(r"\bgrant\s+all\b", re.IGNORECASE),  # privilege escalation
    re.compile(r"\bexec\s+sp_executesql\b", re.IGNORECASE),  # SQL Server dynamic SQL
    re.compile(r"\bload_file\b\s*\(", re.IGNORECASE),  # MySQL file read
    re.compile(r"\binto\s+outfile\b", re.IGNORECASE),  # MySQL file write
    re.compile(r"\bcopy\s+.*\s+from\s+.*://", re.IGNORECASE),  # PostgreSQL COPY from URL

    # === SQL 注入特征 ===
    re.compile(r"union\s+(all\s+)?select\b", re.IGNORECASE),
    re.compile(r"union\s+(all\s+)?\(", re.IGNORECASE),
    re.compile(r";\s*--\s*$", re.MULTILINE),  # SQL comment injection
    re.compile(r"'\s+or\s+'\d+'\s*=\s*'\d+", re.IGNORECASE),  # classic OR injection
    re.compile(r"'\s+or\s+1\s*=\s*1", re.IGNORECASE),  # tautology injection
    re.compile(r"exec\s*\(\s*@", re.IGNORECASE),  # SQL Server command execution
    re.compile(r"xp_cmdshell\b", re.IGNORECASE),  # SQL Server command shell
    re.compile(r"sp_executesql\b", re.IGNORECASE),  # SQL Server dynamic SQL

    # === 网络危险操作 ===
    re.compile(r"\bcurl\s+", re.IGNORECASE),  # network download
    re.compile(r"\bwget\s+", re.IGNORECASE),  # network download
    re.compile(r"\bnc\s+-[elpu]\b", re.IGNORECASE),  # netcat with flags
    re.compile(r"\bncat\b", re.IGNORECASE),  # netcat alternative
    re.compile(r"\bsocat\b", re.IGNORECASE),  # socket tool
    re.compile(r"\bsh\s+-i\b", re.IGNORECASE),  # interactive shell spawn
    re.compile(r"\bbash\s+-i\b", re.IGNORECASE),  # interactive bash
    re.compile(r"\bexec\s+/bin/(sh|bash)\b", re.IGNORECASE),  # shell execution
    re.compile(r"\bpython\s+-c\s+['\"](.*socket|.*subprocess)", re.IGNORECASE),  # socket/subprocess spawn
    re.compile(r"\beval\s+\$\(", re.IGNORECASE),  # command substitution
    re.compile(r"\bexport\s+\w+=\$\(", re.IGNORECASE),  # env variable command injection
    re.compile(r"\b:\s*\{\s*:\s*\|\s*:\s*&\s*\}", re.IGNORECASE),  # fork bomb

    # === 敏感路径访问 ===
    re.compile(r"/\.\./", re.IGNORECASE),  # path traversal
    re.compile(r"(^|/)\.\./", re.IGNORECASE),  # path traversal at start
    re.compile(r"\.(ssh|aws|config|env|git)/?", re.IGNORECASE),  # sensitive directories
    re.compile(r"/etc/passwd", re.IGNORECASE),  # passwd file access
    re.compile(r"/etc/shadow", re.IGNORECASE),  # shadow file access
    re.compile(r"~/.ssh/authorized_keys", re.IGNORECASE),  # SSH authorized keys
    re.compile(r"aws_access_key|aws_secret_key", re.IGNORECASE),  # AWS credentials
    re.compile(r"-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----", re.IGNORECASE),  # private key exposure

    # === 系统修改/配置 ===
    re.compile(r"\biptables\s+-", re.IGNORECASE),  # firewall modification
    re.compile(r"\bnft\s+", re.IGNORECASE),  # nftables firewall
    re.compile(r"\bsysctl\s+", re.IGNORECASE),  # kernel parameter modification
    re.compile(r"\bmount\s+", re.IGNORECASE),  # filesystem mount
    re.compile(r"\bumount\s+", re.IGNORECASE),  # filesystem unmount
    re.compile(r"\bswapoff\b", re.IGNORECASE),  # disable swap
    re.compile(r"\bkill\s+-9\b", re.IGNORECASE),  # force kill
    re.compile(r"\bpkill\s+-9\b", re.IGNORECASE),  # force process kill
    re.compile(r"\breboot\b", re.IGNORECASE),  # system reboot
    re.compile(r"\bshutdown\s+", re.IGNORECASE),  # system shutdown
    re.compile(r"\binit\s+0\b", re.IGNORECASE),  # init level 0 (halt)
    re.compile(r"\bhalt\b", re.IGNORECASE),  # halt system
    re.compile(r"\bpoweroff\b", re.IGNORECASE),  # power off system
    re.compile(r"\bmodprobe\s+", re.IGNORECASE),  # kernel module loading
    re.compile(r"\brmmod\b", re.IGNORECASE),  # kernel module removal
    re.compile(r"\bchroot\b", re.IGNORECASE),  # change root directory
    re.compile(r"\bnsenter\b", re.IGNORECASE),  # enter namespace
    re.compile(r"\bunshare\b", re.IGNORECASE),  # create namespace

    # === 代码执行危险函数 ===
    re.compile(r"\beval\s*\(\s*(request|input|user)", re.IGNORECASE),  # eval with user input
    re.compile(r"\bexec\s*\(\s*(request|input|user)", re.IGNORECASE),  # exec with user input
    re.compile(r"\bos\.system\s*\(", re.IGNORECASE),  # os.system with arbitrary command
    re.compile(r"\bos\.popen\s*\(", re.IGNORECASE),  # os.popen
    re.compile(r"\bsubprocess\.call\s*\(\s*shell\s*=\s*True", re.IGNORECASE),  # shell=True
    re.compile(r"\bsubprocess\.run\s*\(\s*shell\s*=\s*True", re.IGNORECASE),  # shell=True
    re.compile(r"\bsubprocess\.Popen\s*\(\s*shell\s*=\s*True", re.IGNORECASE),  # shell=True
    re.compile(r"\b__import__\s*\(\s*(os|sys|subprocess|pty)", re.IGNORECASE),  # dynamic import
    re.compile(r"\bcompile\s*\(\s*.*\s*,\s*'run_time'", re.IGNORECASE),  # runtime compilation
    re.compile(r"\bgetattr\s*\(\s*getattr\s*\(", re.IGNORECASE),  # chained getattr for bypass
    re.compile(r"\bsetattr\s*\(\s*.*,\s*'__", re.IGNORECASE),  # attribute manipulation
)


@dataclass(frozen=True)
class FenceResult:
    allowed: bool
    matched: str | None = None


def fence_check_text(text: str, extra_patterns: list[str] | None = None) -> FenceResult:
    for pat in _DEFAULT_PATTERNS:
        m = pat.search(text)
        if m:
            return FenceResult(allowed=False, matched=m.group(0))
    if extra_patterns:
        for raw in extra_patterns:
            if re.search(raw, text, re.IGNORECASE):
                return FenceResult(allowed=False, matched=raw)
    return FenceResult(allowed=True)
