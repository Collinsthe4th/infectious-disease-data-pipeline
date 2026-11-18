from src.demo.synthetic import simulate, write_demo_files


def test_simulation_is_consistent():
    df = simulate(end=(2021, 30), seed=1)
    assert set(df["disease_code"]) == {"LASSA", "CHOLERA", "MEASLES", "CSM", "YELLOW_FEVER", "DIPHTHERIA", "MPOX"}
    assert (df["confirmed"] <= df["suspected"]).all()
    lassa = df[(df.disease_code == "LASSA") & (df.epi_week == 10)]
    nat = lassa[lassa.state == "National"].iloc[0]
    assert lassa[lassa.state != "National"]["suspected"].sum() == nat["suspected"]
    last = df[(df.disease_code == "MEASLES") & (df.epi_week == 30)].iloc[0]
    assert last["cum_suspected"] == df[(df.disease_code == "MEASLES")]["suspected"].sum()


def test_demo_files_are_labelled_synthetic(tmp_path):
    files = write_demo_files(tmp_path, end=(2021, 20), pdf_weeks=1)
    assert files and all(f.name.startswith("SYNTHETIC_") for f in files)
    assert any(f.suffix == ".pdf" for f in files)
