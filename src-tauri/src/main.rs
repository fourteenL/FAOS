// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{convert::Infallible, error::Error, path::PathBuf};

use pyo3::wrap_pymodule;
use pytauri::standalone::{
    dunce::simplified, PythonInterpreterBuilder, PythonInterpreterEnv, PythonScript,
};
use faos_app_lib::{ext_mod, tauri_generate_context};

fn main() -> Result<Infallible, Box<dyn Error>> {
    // `cfg(dev)` 由 build.rs 中的 tauri-build 在 `tauri dev` 时设置：
    // dev 用仓库 .venv；打包（tauri build）用嵌入资源目录中的 standalone Python。
    let py_env = if cfg!(dev) {
        let mut venv_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        venv_dir.pop();
        venv_dir.push(".venv");
        assert!(
            venv_dir.is_dir(),
            "Python virtual environment not found at: {}",
            venv_dir.display()
        );
        PythonInterpreterEnv::Venv(venv_dir.into())
    } else {
        let context = tauri_generate_context();
        let resource_dir = tauri::utils::platform::resource_dir(
            context.package_info(),
            &tauri::Env::default(),
        )?;
        // 去掉 `\\?\` UNC 前缀，Python 生态不喜欢它
        PythonInterpreterEnv::Standalone(simplified(&resource_dir).to_owned().into())
    };

    // 等价于 `python -m faos_app`
    let py_script = PythonScript::Module("faos_app".into());

    let builder =
        PythonInterpreterBuilder::new(py_env, py_script, |py| wrap_pymodule!(ext_mod)(py));
    let interpreter = builder.build()?;

    let exit_code = interpreter.run();
    std::process::exit(exit_code);
}
