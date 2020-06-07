# Import the example classes
import pickle

import fire
import numpy as np
# Useful imports
# from example_save_trials import *
import tensorflow as tf
# Import the necessary garage classes
# from garage.baselines.linear_feature_baseline import LinearFeatureBaseline
# from garage.misc import logger
from garage.experiment import run_experiment
from garage.tf.experiment import LocalTFRunner

import ast_toolbox.mcts.BoundedPriorityQueues as BPQ
from ast_toolbox.algos import MCTS
from ast_toolbox.algos import MCTSBV
from ast_toolbox.algos import MCTSRS
# Import the AST classes
from ast_toolbox.envs import ASTEnv
from ast_toolbox.rewards import ExampleAVReward
from ast_toolbox.samplers import ASTVectorizedSampler
from ast_toolbox.simulators import ExampleAVSimulator
from ast_toolbox.spaces import ExampleAVSpaces


def runner(
    mcts_type=None,
    env_args=None,
    run_experiment_args=None,
    sim_args=None,
    reward_args=None,
    spaces_args=None,
    algo_args=None,
    runner_args=None,
    bpq_args=None,
    # log_dir='.',
):
    if mcts_type is None:
        mcts_type = 'mcts'

    if env_args is None:
        env_args = {}

    if run_experiment_args is None:
        run_experiment_args = {}

    if sim_args is None:
        sim_args = {}

    if reward_args is None:
        reward_args = {}

    if spaces_args is None:
        spaces_args = {}

    if algo_args is None:
        algo_args = {}

    if runner_args is None:
        runner_args = {}

    if bpq_args is None:
        bpq_args = {}

    if 'n_parallel' in run_experiment_args:
        n_parallel = run_experiment_args['n_parallel']
    else:
        n_parallel = 1
        run_experiment_args['n_parallel'] = n_parallel

    if 'max_path_length' in sim_args:
        max_path_length = sim_args['max_path_length']
    else:
        max_path_length = 50
        sim_args['max_path_length'] = max_path_length

    if 'batch_size' in runner_args:
        batch_size = runner_args['batch_size']
    else:
        batch_size = max_path_length * n_parallel
        runner_args['batch_size'] = batch_size

    def run_task(snapshot_config, *_):

        seed = 0
        # top_k = 10
        np.random.seed(seed)

        config = tf.ConfigProto()
        config.gpu_options.allow_growth = True
        with tf.Session(config=config) as sess:
            with tf.variable_scope('AST', reuse=tf.AUTO_REUSE):

                with LocalTFRunner(
                        snapshot_config=snapshot_config, max_cpus=4, sess=sess) as local_runner:

                    # Instantiate the example classes
                    sim = ExampleAVSimulator(**sim_args)
                    reward_function = ExampleAVReward(**reward_args)
                    spaces = ExampleAVSpaces(**spaces_args)

                    # Create the environment
                    if 'id' in env_args:
                        env_args.pop('id')
                    env = ASTEnv(simulator=sim,
                                 reward_function=reward_function,
                                 spaces=spaces,
                                 **env_args
                                 )

                    top_paths = BPQ.BoundedPriorityQueue(**bpq_args)

                    if mcts_type == 'mcts':
                        print('mcts')
                        algo = MCTS(env=env,
                                    top_paths=top_paths,
                                    **algo_args)
                    elif mcts_type == 'mctsbv':
                        print('mctsbv')
                        algo = MCTSBV(env=env,
                                      top_paths=top_paths,
                                      **algo_args)
                    elif mcts_type == 'mctsrs':
                        print('mctsrs')
                        algo = MCTSRS(env=env,
                                      top_paths=top_paths,
                                      **algo_args)
                    else:
                        raise NotImplementedError

                    sampler_cls = ASTVectorizedSampler

                    local_runner.setup(algo=algo,
                                       env=env,
                                       sampler_cls=sampler_cls,
                                       sampler_args={"open_loop": False,
                                                     "sim": sim,
                                                     "reward_function": reward_function,
                                                     "n_envs": n_parallel})

                    # Run the experiment
                    local_runner.train(**runner_args)

                    log_dir = run_experiment_args['log_dir']
                    with open(log_dir + '/best_actions.p', 'rb') as f:
                        best_actions = pickle.load(f)
                    expert_trajectories = []
                    for actions in best_actions:
                        sim.reset(s_0=env_args['s_0'])
                        path = []
                        for action in actions:
                            obs = sim.step(action)
                            state = sim.clone_state()
                            reward = reward_function.give_reward(
                                action=action,
                                info=sim.get_reward_info())
                            path.append({'state': state,
                                         'reward': reward,
                                         'action': action,
                                         'observation': obs})
                        expert_trajectories.append(path)
                    with open(log_dir + '/expert_trajectory.p', 'wb') as f:
                        pickle.dump(expert_trajectories, f)
    run_experiment(
        run_task,
        **run_experiment_args,
    )


if __name__ == '__main__':
    fire.Fire()
