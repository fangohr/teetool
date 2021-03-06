"""
<example>
"""

import teetool as tt  # core


llsettings = []

llsettings.append(["resampling", 100, "", "", 0])
llsettings.append(["resampling", 100, "", "", .5])

llsettings.append(["ML", 100, "bernstein", 5, .5])
llsettings.append(["ML", 100, "rbf", 10, .5])

"""
llsettings.append(["EM", 100, "bernstein", 5, .5])
llsettings.append(["EM", 100, "rbf", 10, .5])
"""

for ls in llsettings:

    settings = {"model_type":ls[0],
                "ngaus":ls[1],
                "basis_type":ls[2],
                "nbasis":ls[3]}

    # build world
    world_name = "[{0}] [{1}] [{2}] [{3}]".format(settings["model_type"],
                                  settings["basis_type"],
                                  settings["nbasis"],
                                  ls[4])

    # create a new world
    new_world = tt.World(name=world_name, ndim=2, resolution=[50, 50, 50])

    # add trajectories
    for ntype in [0, 1]:
        cluster_name = "toy {0}".format(ntype)
        cluster_data = tt.helpers.get_trajectories(ntype,
                                                   ndim=2,
                                                   ntraj=50,
                                                   npoints=100,
                                                   noise_std=ls[4])
        new_world.addCluster(cluster_data, cluster_name)

    # output an overview
    new_world.overview()

    # build the model
    new_world.buildModel(settings)

    # modify default resolution
    #new_world.setResolution(xstep=100, ystep=100)

    # output an overview
    new_world.overview()

    # visuals by matplotlib
    for ntype in [0, 1]:
        visual = tt.visual_2d.Visual_2d(new_world)
        visual.plotTrajectories([ntype])
        visual.plotSamples([ntype])
        visual.plotLogLikelihood([ntype], pmin=.9, pmax=1)
        visual.plotLegend()
        visual.save(ntype)
        visual.close()

    visual = tt.visual_2d.Visual_2d(new_world)
    visual.plotTrajectories([0, 1])
    visual.plotLogLikelihood([0, 1], pmin=.9, pmax=1.0)
    visual.save('i')
    visual.close()

# show [ wait for user input ]
#visual.show()
