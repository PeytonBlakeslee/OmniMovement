cmake_minimum_required(VERSION 3.24)
project(omni_precision_backend_build LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(UNREALSDK_FLAVOUR WILLOW CACHE STRING "UnrealSDK game flavour")
add_subdirectory(pyunrealsdk EXCLUDE_FROM_ALL)
pyunrealsdk_add_module(omni_precision_backend
    omni_precision_backend.cpp
    ../native_core/omni_native_core.cpp)

target_include_directories(omni_precision_backend PRIVATE ../native_core)
