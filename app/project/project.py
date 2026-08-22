import shutil
import yaml
from pathlib import Path

from app.runtime.manifest import RuntimeManifest
from app.runtime.startup import ensure_startup_scripts


class Project:
    def __init__(self, name: str, base_dir: str, language: str = "Python", version: str = "0.1"):
        self.name = name
        # 将基础路径和项目名拼起来，比如 workspace/TestApp
        self.project_path = Path(base_dir) / self.name
        self.language = language
        self.version = version
        self.state = "created"
        self.workspace_path = self.project_path / "workspace"

    def create(self):
        # 第一步：创建项目根目录和 workspace 子目录
        print(f"🚀 [1/3] 正在创建项目目录: {self.project_path} ...")
        self.project_path.mkdir(parents=True, exist_ok=True)
        self.workspace_path.mkdir(parents=True, exist_ok=True)

        # 第二步：声明默认运行时。实际依赖与测试由 Sandbox 管理，不使用宿主 .venv。
        print("📦 [2/3] 正在写入默认 sandbox 运行时声明 ...")
        RuntimeManifest(version=1, profile="python-stdlib").save(
            str(self.project_path)
        )

        # 第三步：生成初始的 YAML 配置文件
        print(f"📄 [3/3] 正在生成项目配置 (project.yaml) ...")
        # 利用 f-string 将当前的 self.name 动态注入进去
        config = {
            "name": self.name,
            "language": self.language,
            "version": self.version
        }

        # 指定文件路径并写入内容，指定 utf-8 编码是个好习惯
        yaml_path = self.project_path / "project.yaml"

        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(config, f, allow_unicode=True)

        ensure_startup_scripts(str(self.project_path), project_id=self.name)



        print("✅ 项目创建完成！")
        print(f"   📁 项目根目录: {self.project_path}")
        print(f"   📂 工作空间:   {self.workspace_path}")

    @classmethod
    def create_at(
        cls,
        project_path: str,
        *,
        name: str,
        language: str = "Python",
        version: str = "0.1",
    ) -> "Project":
        """在用户指定的本地目录创建项目，项目 ID 与目录名可以不同。"""
        project = cls(name=name, base_dir=str(Path(project_path).resolve().parent), language=language, version=version)
        project.project_path = Path(project_path).expanduser().resolve()
        project.workspace_path = project.project_path / "workspace"
        project.create()
        return project

    @staticmethod
    def load(project_dir: str):
        """
        工厂方法：直接传入项目的完整路径（比如 ./workspace/AI-Code-Review），
        读取 YAML 后，帮你把 Project 对象组装好并返回。
        """
        target_path = Path(project_dir)
        yaml_path = target_path / "project.yaml"
        
        if not yaml_path.exists():
            raise FileNotFoundError(f"❌ 找不到项目配置文件: {yaml_path}")
            
        print(f"📂 正在使用 PyYAML 读取配置: {yaml_path} ...")
        
        # 使用 yaml.safe_load 安全读取（行业规范，防止恶意代码执行）
        with yaml_path.open("r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
            
        # 根据读取到的数据，真正在内存里“组装”出这个对象
        # target_path.parent 就是基础目录（比如 Environment）
        loaded_project = Project(
            name=config.get("name", target_path.name), 
            base_dir=str(target_path.parent),
            language=config.get("language", "Python"),
            version=config.get("version", "0.1"),
        )
        
        # 将组装好的对象返回给调用者
        return loaded_project

    @staticmethod
    def delete(name: str, base_dir: str):
        """删除指定项目（包括项目目录内的所有文件）"""
        project_path = Path(base_dir) / name

        if not project_path.exists():
            raise FileNotFoundError(f"❌ 项目不存在: {project_path}")

        shutil.rmtree(project_path)
        print(f"🗑️  项目已删除: {project_path}")

    @staticmethod
    def exists(name: str, base_dir: str) -> bool:
        """判断项目是否存在"""
        project_path = Path(base_dir) / name
        return project_path.exists() and project_path.is_dir()

    @staticmethod
    def list(base_dir: str) -> list[str]:
        """扫描 workspace，返回所有项目名列表"""
        workspace = Path(base_dir)

        if not workspace.exists():
            return []

        return sorted([
            p.name for p in workspace.iterdir()
            if p.is_dir() and (p / "project.yaml").exists()
        ])
