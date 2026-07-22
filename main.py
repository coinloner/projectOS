from app.project.project import Project

def main():
    # 我们把测试项目建在前面规划好的 Environment 目录下
    demo_project = Project(name="AI-Code-Review", base_dir="./Environment")
    
    # 触发创建动作
    demo_project.create()

if __name__ == "__main__":
    main()