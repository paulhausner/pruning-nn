import os
import time
import torch
import torch.nn as nn
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt

from pruning_nn import *
from util import *

# constant variables
hyper_params = {
    'num_retrain_epochs': 2,
    'num_epochs': 200,
    'learning_rate': 0.01,
    'momentum': 0
}

# experiment for 10, 15 and 25 percent of the weights each step.

result_folder = './out/result/'
model_folder = './out/model/'

test_set = dataloader.get_test_dataset()
train_set, valid_set = dataloader.get_train_valid_dataset(valid_batch=100)
loss_func = nn.CrossEntropyLoss()


def setup():
    if not os.path.exists('./out'):
        os.mkdir('./out')
    if not os.path.exists('./out/model'):
        os.mkdir('./out/model')
    if not os.path.exists('./out/result'):
        os.mkdir('./out/result')


def setup_training(model, lr=0.01, mom=0.0):
    optimizer = torch.optim.SGD(model.parameters(), lr=lr, momentum=mom)
    return optimizer


def train_network(filename='model'):
    # create neural net and train (input is image, output is number between 0 and 9.
    model = network.NeuralNetwork(28 * 28, 100, 10)
    # if multi_layer:
    #    model = MultiLayerNeuralNetwork(28 * 28, 30, 10)
    # model = LeNet300_100(28 * 28, 10)
    # print(get_network_weight_count(model))

    # train and test the network
    t = True
    epoch = 0
    prev_acc = 0

    lr = hyper_params['learning_rate']
    optimizer = setup_training(model, lr=lr, mom=0.5)

    while t and epoch < hyper_params['num_epochs']:
        learning.train(train_set, model, optimizer, loss_func)
        new_acc = learning.test(valid_set, model)

        if new_acc - prev_acc < 0.001:
            if lr > 0.0001:
                # adjust learning rate
                lr = lr * 0.1
                optimizer = setup_training(model, lr=lr, mom=0.5)
            else:
                # stop training
                t = False

        epoch += 1
        prev_acc = new_acc

    acc = learning.test(test_set, model)
    print('Needed ' + str(epoch) + ' epochs to train model to accuracy: ' + str(acc) + ' model: ' + filename)

    # save the current model
    torch.save(model, model_folder + filename + '.pt')


def train_sparse_model(filename='model', save=False):
    model = torch.load(result_folder + filename + '.pt')
    pruned_acc = learning.test(test_set, model)
    optimizer = setup_training(model)

    s = pd.DataFrame(columns=['epoch', 'test_acc'])
    s = s.append({'epoch': -1, 'test_acc': pruned_acc}, ignore_index=True)

    # todo: use early stopping or something else to stop training for this particular model
    for epoch in range(hyper_params['num_epochs']):
        learning.train(train_set, model, optimizer, loss_func)
        tr = learning.test(test_set, model)
        s = s.append({'epoch': epoch, 'test_acc': tr}, ignore_index=True)
        print(epoch, tr)

    final_acc = learning.test(test_set, model)
    print(pruned_acc, final_acc)

    s.to_pickle(result_folder + filename + '-scatch.pkl')
    if save:
        torch.save(model, result_folder + filename + '-scratch.pt')


def prune_network(prune_strategy, pruning_rates=None, filename='model', runs=1, variable_retraining=False, save=False):
    if pruning_rates is None:
        pruning_rates = [70, 60, 50, 40, 25]

    # prune using strategy
    strategy = pruning.PruneNeuralNetStrategy(prune_strategy)

    # calculate the loss of the network if it is needed by the pruning method for the saliency calculation
    if strategy.requires_loss():
        # if optimal brain damage is used get dataset with only one batch
        if prune_strategy == pruning.optimal_brain_damage:
            btx = None
        else:
            btx = 100
        _, strategy.valid_dataset = learning.get_train_valid_dataset(valid_batch=btx)
        strategy.criterion = loss_func

    # output variables
    out_name = result_folder + str(prune_strategy.__name__) + '-var=' + str(variable_retraining) + '-' + filename
    s = pd.DataFrame(columns=['run', 'accuracy', 'pruning_perc', 'number_of_weights', 'pruning_method'])

    # set variables for the best models with initial values.
    best_acc = 0
    smallest_model = 30000

    # prune with different pruning rates
    for rate in pruning_rates:

        # repeat all experiments a fixed number of times
        for i in range(runs):
            # load model
            model = torch.load(model_folder + filename + '.pt')

            # check original values from model
            original_acc = learning.test(test_set, model)
            original_weight_count = util.get_network_weight_count(model)

            # loss and optimizer for the loaded model
            optimizer = setup_training(model)

            # prune as long as there are more than 500 elements in the network
            while util.get_network_weight_count(model).item() > 500:
                # start pruning
                start = time.time()
                strategy.prune(model, rate)

                # Retrain and reevaluate the process
                if strategy.require_retraining():
                    # test the untrained performance
                    untrained_test_acc = learning.test(test_set, model)
                    untrained_acc = learning.test(valid_set, model)

                    # setup variables for loop retraining
                    prev_acc = untrained_acc
                    retrain = True
                    retrain_epoch = 1

                    # continue retraining for variable time
                    while retrain:
                        learning.train(train_set, model, optimizer, loss_func)
                        new_acc = learning.test(valid_set, model)

                        # stop retraining if the test accuracy imporves only slightly or the maximum number of
                        # retrainnig epochs is reached
                        if (variable_retraining and new_acc - prev_acc < 0.0001) \
                                or retrain_epoch >= hyper_params['num_retrain_epochs']:
                            retrain = False
                        else:
                            retrain_epoch += 1
                            prev_acc = new_acc

                    final_acc = learning.test(test_set, model)
                    retrain_change = final_acc - untrained_test_acc
                else:
                    retrain_epoch = 0
                    final_acc = learning.test(test_set, model)
                    retrain_change = 0

                # Save the best models with the following criterion
                # 1. smallest weight count with max 1% accuracy drop from the original model.
                # 2. best performing model overall with at least a compression rate of 50%.
                if save and (
                        (original_acc - final_acc < 1 and util.get_network_weight_count(model) < smallest_model) or (
                        util.get_network_weight_count(model) <= original_weight_count / 2 and final_acc > best_acc)):
                    # set the values to the new ones
                    best_acc = final_acc if final_acc > best_acc else best_acc
                    model_size = int(util.get_network_weight_count(model))
                    smallest_model = model_size if model_size < smallest_model else smallest_model

                    # save the model
                    torch.save(model, out_name + '-rate{}-weight{}-per{}.pt'
                               .format(str(rate), str(model_size), str(final_acc)))

                # evaluate duration of process
                time_needed = time.time() - start

                # accumulate data
                tmp = pd.DataFrame({'run': [i],
                                    'accuracy': [final_acc],
                                    'pruning_perc': [rate],
                                    'number_of_weights': [util.get_network_weight_count(model).item()],
                                    'pruning_method': [str(prune_strategy.__name__)],
                                    'time': [time_needed],
                                    'retrain_change': [retrain_change],
                                    'retrain_epochs': [retrain_epoch]
                                    })
                s = s.append(tmp, ignore_index=True, sort=True)

        # save data frame
        s.to_pickle(out_name + '.pkl')


def train_models(num=10):
    for i in range(num):
        train_network('model' + str(i))


def experiment1():
    for j in range(4):
        model = 'model' + str(j)
        s_m = (j == 0)  # save models from the first model only which is the highest performing one...

        for strat in [pruning.random_pruning, pruning.magnitude_class_distributed, pruning.magnitude_class_uniform,
                      pruning.magnitude_class_blinded]:
            prune_network(prune_strategy=strat, filename=model, runs=25, save=s_m)

    prune_network(prune_strategy=pruning.optimal_brain_damage, filename='model0', runs=25, save=True)


def experiment2():
    for strat in [pruning.random_pruning, pruning.magnitude_class_distributed, pruning.magnitude_class_uniform,
                  pruning.magnitude_class_blinded, pruning.optimal_brain_damage]:
        # variable retraining
        hyper_params['num_retrain_epochs'] = 10
        prune_network(prune_strategy=strat, pruning_rates=[50], filename='model', runs=25, variable_retraining=True,
                      save=True)

        # non-variable retraining
        hyper_params['num_retrain_epochs'] = 2
        prune_network(prune_strategy=strat, pruning_rates=[50], filename='model', runs=25, variable_retraining=False,
                      save=True)


def experiment3():
    # todo: implement experiment 3 with fixed value pruning either as funciton in main or as single pruning strategies
    # will use either fixed or variable retraining depending on the results from experiment one and two
    # 25 runs, 1 model
    pass


def experiment4():
    # todo: implement single big bang pruning. This will include L-OBS as a pruning technique. Uses variable retraining
    # wit a maximum of 20 retraining epochs and uses 25 runs, 1 model
    pass


if __name__ == '__main__':
    # setup environment
    setup()
    experiment2()
